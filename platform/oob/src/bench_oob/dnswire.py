"""Just enough DNS wire format to answer a query and log the name that was resolved.

Hand-rolled rather than pulled from a library: we parse one question and emit A, TXT,
NS or SOA records, which is a hundred lines, whereas a real server would drag in zone
files, recursion and a dependency we would have to keep patched.

Known limits, all deliberate:

* one question per message (every real client sends exactly one);
* EDNS0 OPT records in the request are ignored and never echoed, so a client that
  advertised a large UDP buffer gets a plain 512-byte-class answer;
* no DNSSEC, no zone transfer, no recursion (queries outside our zone are REFUSED);
* compression pointers are followed when parsing, and used in answers only to point at
  the question name at offset 12.
"""

from __future__ import annotations

import socket
import struct
from dataclasses import dataclass, field

TYPE_A = 1
TYPE_NS = 2
TYPE_CNAME = 5
TYPE_SOA = 6
TYPE_PTR = 12
TYPE_MX = 15
TYPE_TXT = 16
TYPE_AAAA = 28
TYPE_SRV = 33
TYPE_ANY = 255

TYPE_NAMES = {
    TYPE_A: "A",
    TYPE_NS: "NS",
    TYPE_CNAME: "CNAME",
    TYPE_SOA: "SOA",
    TYPE_PTR: "PTR",
    TYPE_MX: "MX",
    TYPE_TXT: "TXT",
    TYPE_AAAA: "AAAA",
    TYPE_SRV: "SRV",
    TYPE_ANY: "ANY",
}

CLASS_IN = 1
CLASS_ANY = 255

RCODE_NOERROR = 0
RCODE_FORMERR = 1
RCODE_NXDOMAIN = 3
RCODE_NOTIMP = 4
RCODE_REFUSED = 5

MAX_UDP = 512


class DnsFormatError(ValueError):
    """The bytes were not a parseable DNS message."""


def type_name(qtype: int) -> str:
    return TYPE_NAMES.get(qtype, f"TYPE{qtype}")


@dataclass(frozen=True)
class Question:
    labels: tuple[bytes, ...]
    qtype: int
    qclass: int

    @property
    def name(self) -> str:
        """Dotted, lowercased, byte-preserving (latin-1) rendering for logs and matching."""
        return ".".join(label.decode("latin-1") for label in self.labels).lower()


@dataclass(frozen=True)
class Query:
    txid: int
    flags: int
    question: Question | None
    qdcount: int = 0
    raw: bytes = b""
    trailing: bytes = field(default=b"", repr=False)

    @property
    def opcode(self) -> int:
        return (self.flags >> 11) & 0x0F

    @property
    def recursion_desired(self) -> bool:
        return bool(self.flags & 0x0100)


def parse_name(data: bytes, offset: int, depth: int = 0) -> tuple[list[bytes], int]:
    """Return the labels at ``offset`` and the offset just after the name.

    Follows compression pointers; ``depth`` guards against pointer loops, which is the
    classic way to hang a naive parser."""
    if depth > 10:
        raise DnsFormatError("compression pointer loop")
    labels: list[bytes] = []
    pos = offset
    jumped_to: int | None = None
    while True:
        if pos >= len(data):
            raise DnsFormatError("name runs past end of message")
        length = data[pos]
        if length == 0:
            pos += 1
            break
        if length & 0xC0 == 0xC0:
            if pos + 1 >= len(data):
                raise DnsFormatError("truncated compression pointer")
            pointer = ((length & 0x3F) << 8) | data[pos + 1]
            sub, _ = parse_name(data, pointer, depth + 1)
            labels.extend(sub)
            pos += 2
            jumped_to = pos
            break
        if length & 0xC0:
            raise DnsFormatError(f"reserved label type 0x{length:02x}")
        end = pos + 1 + length
        if end > len(data):
            raise DnsFormatError("label runs past end of message")
        labels.append(data[pos + 1 : end])
        pos = end
    return labels, (jumped_to if jumped_to is not None else pos)


def parse_query(data: bytes) -> Query:
    if len(data) < 12:
        raise DnsFormatError("message shorter than a DNS header")
    txid, flags, qdcount, _ancount, _nscount, _arcount = struct.unpack("!6H", data[:12])
    if qdcount == 0:
        return Query(txid=txid, flags=flags, question=None, qdcount=0, raw=data)
    labels, pos = parse_name(data, 12)
    if pos + 4 > len(data):
        raise DnsFormatError("truncated question")
    qtype, qclass = struct.unpack("!2H", data[pos : pos + 4])
    question = Question(tuple(labels), qtype, qclass)
    return Query(
        txid=txid,
        flags=flags,
        question=question,
        qdcount=qdcount,
        raw=data,
        trailing=data[pos + 4 :],
    )


def encode_name(name: str | bytes | tuple[bytes, ...] | list[bytes]) -> bytes:
    if isinstance(name, (tuple, list)):
        labels = [bytes(label) for label in name]
    else:
        text = name.decode("latin-1") if isinstance(name, bytes) else name
        labels = [
            part.encode("latin-1") for part in text.strip(".").split(".") if part
        ]
    out = bytearray()
    for label in labels:
        if len(label) > 63:
            raise DnsFormatError("label longer than 63 bytes")
        out.append(len(label))
        out += label
    out.append(0)
    return bytes(out)


def a_rdata(ip: str) -> bytes:
    return socket.inet_aton(ip)


def txt_rdata(text: str) -> bytes:
    payload = text.encode("utf-8")[:255]
    return bytes([len(payload)]) + payload


def soa_rdata(zone: str, serial: int = 1) -> bytes:
    mname = encode_name(f"ns.{zone}")
    rname = encode_name(f"hostmaster.{zone}")
    # refresh / retry / expire / minimum: small values, this zone is ephemeral and we
    # actively want resolvers to come back rather than cache us for a day.
    return mname + rname + struct.pack("!5I", serial, 60, 60, 3600, 5)


def _record(name_field: bytes, rrtype: int, ttl: int, rdata: bytes) -> bytes:
    return name_field + struct.pack("!HHIH", rrtype, CLASS_IN, ttl, len(rdata)) + rdata


QNAME_POINTER = b"\xc0\x0c"  # offset 12: where we always write the question name.


def build_response(
    query: Query,
    *,
    answers: list[tuple[int, bytes]] | None = None,
    authority: list[tuple[str, int, bytes]] | None = None,
    rcode: int = RCODE_NOERROR,
    authoritative: bool = True,
    ttl: int = 5,
) -> bytes:
    """Assemble a response to ``query``.

    ``answers`` are ``(type, rdata)`` for the queried name; ``authority`` entries carry
    their own owner name because a SOA is owned by the zone apex, not by the qname."""
    answers = answers or []
    authority = authority or []
    flags = 0x8000  # QR
    flags |= (query.opcode & 0x0F) << 11
    if authoritative:
        flags |= 0x0400  # AA
    if query.recursion_desired:
        flags |= 0x0100  # echo RD; RA stays clear, we do not recurse
    flags |= rcode & 0x0F

    question_bytes = b""
    qdcount = 0
    if query.question is not None:
        question_bytes = encode_name(query.question.labels) + struct.pack(
            "!2H", query.question.qtype, query.question.qclass
        )
        qdcount = 1

    body = b"".join(_record(QNAME_POINTER, rrtype, ttl, rdata) for rrtype, rdata in answers)
    ns = b"".join(
        _record(encode_name(owner), rrtype, ttl, rdata) for owner, rrtype, rdata in authority
    )
    header = struct.pack("!6H", query.txid, flags, qdcount, len(answers), len(authority), 0)
    message = header + question_bytes + body + ns
    if len(message) > MAX_UDP:
        # Truncate to the header+question and set TC so the client retries over TCP.
        message = struct.pack(
            "!6H", query.txid, flags | 0x0200, qdcount, 0, 0, 0
        ) + question_bytes
    return message


def build_format_error(data: bytes) -> bytes:
    """Answer to bytes we could not parse: echo the transaction id if there was one."""
    txid = struct.unpack("!H", data[:2])[0] if len(data) >= 2 else 0
    return struct.pack("!6H", txid, 0x8000 | RCODE_FORMERR, 0, 0, 0, 0)
