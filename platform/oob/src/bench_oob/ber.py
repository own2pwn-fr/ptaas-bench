"""Minimal BER reader/writer, only as much as LDAPv3 messages need.

Limits, stated plainly because a half-implemented codec that pretends otherwise is a
trap:

* single-byte identifiers only. Tag numbers above 30 (multi-byte identifier octets)
  are rejected. Every LDAP protocol op and every context tag we care about is <= 30.
* definite lengths only, up to 4 length octets. RFC 4511 forbids the indefinite form,
  so a message using it is treated as malformed.
* no type checking beyond the tag byte: we read the structure, not the semantics.
"""

from __future__ import annotations

from dataclasses import dataclass

TAG_BOOLEAN = 0x01
TAG_INTEGER = 0x02
TAG_OCTET_STRING = 0x04
TAG_NULL = 0x05
TAG_ENUMERATED = 0x0A
TAG_SEQUENCE = 0x30
TAG_SET = 0x31

# APPLICATION, constructed: 0x40 | 0x20 | n
APP_BIND_REQUEST = 0x60
APP_BIND_RESPONSE = 0x61
APP_UNBIND_REQUEST = 0x42  # APPLICATION 2, primitive (it carries no data)
APP_SEARCH_REQUEST = 0x63
APP_SEARCH_ENTRY = 0x64
APP_SEARCH_DONE = 0x65
APP_EXTENDED_REQUEST = 0x77
APP_EXTENDED_RESPONSE = 0x78

OP_NAMES = {
    APP_BIND_REQUEST: "bind",
    APP_UNBIND_REQUEST: "unbind",
    APP_SEARCH_REQUEST: "search",
    APP_EXTENDED_REQUEST: "extended",
}


class BerError(ValueError):
    """The bytes were not parseable BER (or used a form we do not implement)."""


@dataclass(frozen=True)
class Tlv:
    tag: int
    content: bytes
    end: int

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", "replace")

    @property
    def int(self) -> int:
        return int.from_bytes(self.content, "big", signed=True) if self.content else 0


def read_length(buf: bytes, pos: int) -> tuple[int, int]:
    if pos >= len(buf):
        raise BerError("truncated length")
    first = buf[pos]
    pos += 1
    if first < 0x80:
        return first, pos
    if first == 0x80:
        raise BerError("indefinite length is not allowed in LDAP")
    count = first & 0x7F
    if count > 4:
        raise BerError("length field too large")
    if pos + count > len(buf):
        raise BerError("truncated long-form length")
    return int.from_bytes(buf[pos : pos + count], "big"), pos + count


def read_tlv(buf: bytes, pos: int = 0) -> Tlv:
    if pos >= len(buf):
        raise BerError("truncated element")
    tag = buf[pos]
    if tag & 0x1F == 0x1F:
        raise BerError("multi-byte tag numbers are not supported")
    length, pos = read_length(buf, pos + 1)
    end = pos + length
    if end > len(buf):
        raise BerError("element runs past end of buffer")
    return Tlv(tag=tag, content=buf[pos:end], end=end)


def iter_tlv(buf: bytes):
    pos = 0
    while pos < len(buf):
        element = read_tlv(buf, pos)
        yield element
        pos = element.end


def encode_length(length: int) -> bytes:
    if length < 0x80:
        return bytes([length])
    raw = length.to_bytes((length.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(raw)]) + raw


def tlv(tag: int, content: bytes) -> bytes:
    return bytes([tag]) + encode_length(len(content)) + content


def enc_int(value: int, tag: int = TAG_INTEGER) -> bytes:
    length = max(1, (value.bit_length() + 8) // 8)
    return tlv(tag, value.to_bytes(length, "big", signed=True))


def enc_enum(value: int) -> bytes:
    return enc_int(value, TAG_ENUMERATED)


def enc_str(value: str | bytes, tag: int = TAG_OCTET_STRING) -> bytes:
    raw = value.encode("utf-8") if isinstance(value, str) else value
    return tlv(tag, raw)


def enc_seq(*parts: bytes) -> bytes:
    return tlv(TAG_SEQUENCE, b"".join(parts))


def enc_set(*parts: bytes) -> bytes:
    return tlv(TAG_SET, b"".join(parts))
