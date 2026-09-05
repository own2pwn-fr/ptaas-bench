"""DNS listener: UDP and TCP, authoritative-ish for BENCH_OOB_DOMAIN.

Two jobs at once. It logs the resolution -- which is the whole callback for a blind
SSRF or a DNS-only exfiltration payload -- and it answers A with an address the client
can actually connect to, so that ``http://<token>.oob.bench.local/x`` continues into an
HTTP callback instead of dying at name resolution.

The A answer is per-client: whichever of our addresses faces the querying host (see
net.local_address_towards), or BENCH_OOB_PUBLIC_IP when set. On a dual-homed container
a fixed answer would hand out the wrong network half the time.

Queries outside the zone get REFUSED, not resolved: we are not a recursive resolver,
and a tool must not be able to use us as an open resolver on bench-public. They are
still recorded, because a query for someone else's collaborator domain arriving here
says something about the tool.
"""

from __future__ import annotations

import asyncio
import logging

from .. import dnswire
from ..config import Config
from ..net import local_address_towards
from ..recorder import Recorder
from ..tokens import Candidate, host_label

log = logging.getLogger("bench_oob.dns")

TXT_ANSWER = "ptaas-bench out-of-band canary"


class DnsHandler:
    def __init__(self, config: Config, recorder: Recorder) -> None:
        self.config = config
        self.recorder = recorder
        self._answer_cache: dict[str, str] = {}

    def answer_ip(self, peer_ip: str) -> str:
        if self.config.public_ip:
            return self.config.public_ip
        cached = self._answer_cache.get(peer_ip)
        if cached:
            return cached
        address = local_address_towards(peer_ip, 53, default="127.0.0.1") or "127.0.0.1"
        if len(self._answer_cache) > 1024:  # bounded: one entry per client, not per query
            self._answer_cache.clear()
        self._answer_cache[peer_ip] = address
        return address

    def handle(self, data: bytes, peer_ip: str, proto: str) -> bytes:
        try:
            query = dnswire.parse_query(data)
        except dnswire.DnsFormatError as exc:
            self.recorder.record(
                channel="dns",
                source_ip=peer_ip,
                candidates=[],
                raw=f"dns/{proto} malformed query ({exc}): {data[:256].hex()}",
                detail={"proto": proto, "malformed": True},
                in_zone=None,
            )
            return dnswire.build_format_error(data)

        if query.question is None:
            return dnswire.build_response(query, rcode=dnswire.RCODE_FORMERR, authoritative=False)

        question = query.question
        qname = question.name
        zone = self.config.zone
        in_zone = qname == zone or qname.endswith("." + zone)
        answer_ip = self.answer_ip(peer_ip) if in_zone else None

        self.recorder.record(
            channel="dns",
            source_ip=peer_ip,
            candidates=[Candidate("dns_label", host_label(qname, zone) or "")],
            raw=(
                f"dns/{proto} qname={qname} qtype={dnswire.type_name(question.qtype)} "
                f"qclass={question.qclass} answer={answer_ip or 'refused'}"
            ),
            detail={
                "proto": proto,
                "qname": qname,
                "qtype": dnswire.type_name(question.qtype),
                "answer": answer_ip,
            },
            in_zone=in_zone,
        )

        if not in_zone:
            return dnswire.build_response(
                query, rcode=dnswire.RCODE_REFUSED, authoritative=False
            )

        answers: list[tuple[int, bytes]] = []
        authority: list[tuple[str, int, bytes]] = []
        qtype = question.qtype
        if question.qclass in (dnswire.CLASS_IN, dnswire.CLASS_ANY):
            if qtype in (dnswire.TYPE_A, dnswire.TYPE_ANY) and answer_ip:
                answers.append((dnswire.TYPE_A, dnswire.a_rdata(answer_ip)))
            if qtype in (dnswire.TYPE_TXT, dnswire.TYPE_ANY):
                answers.append((dnswire.TYPE_TXT, dnswire.txt_rdata(TXT_ANSWER)))
            if qtype in (dnswire.TYPE_NS, dnswire.TYPE_ANY):
                answers.append((dnswire.TYPE_NS, dnswire.encode_name(f"ns.{zone}")))
            if qtype == dnswire.TYPE_SOA:
                answers.append((dnswire.TYPE_SOA, dnswire.soa_rdata(zone)))
        if not answers:
            # NOERROR with an empty answer plus the SOA: the honest reply for a name we
            # own but a type we do not serve (AAAA, MX...). Sending NXDOMAIN instead
            # would make a client give up on the name entirely, including for A.
            authority.append((zone, dnswire.TYPE_SOA, dnswire.soa_rdata(zone)))

        return dnswire.build_response(
            query, answers=answers, authority=authority, ttl=self.config.dns_ttl
        )


class DnsUdpProtocol(asyncio.DatagramProtocol):
    def __init__(self, handler: DnsHandler) -> None:
        self.handler = handler
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport) -> None:  # type: ignore[override]
        self.transport = transport

    def datagram_received(self, data: bytes, addr) -> None:  # type: ignore[override]
        try:
            response = self.handler.handle(data, addr[0], "udp")
        except Exception:  # pragma: no cover - a listener must never die on one packet
            log.exception("dns/udp handler failed")
            return
        if self.transport is not None and response:
            self.transport.sendto(response, addr)

    def error_received(self, exc: Exception) -> None:  # pragma: no cover
        log.debug("dns/udp error: %s", exc)


async def handle_tcp(handler: DnsHandler, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """RFC 1035 section 4.2.2 framing: each message is prefixed with its 16-bit length."""
    peer = writer.get_extra_info("peername") or ("?", 0)
    try:
        while True:
            try:
                header = await asyncio.wait_for(reader.readexactly(2), timeout=10)
            except (asyncio.IncompleteReadError, asyncio.TimeoutError, TimeoutError):
                return
            length = int.from_bytes(header, "big")
            if length == 0:
                return
            try:
                payload = await asyncio.wait_for(reader.readexactly(length), timeout=10)
            except (asyncio.IncompleteReadError, asyncio.TimeoutError, TimeoutError):
                return
            response = handler.handle(payload, peer[0], "tcp")
            writer.write(len(response).to_bytes(2, "big") + response)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        raise
    except Exception:  # pragma: no cover
        log.exception("dns/tcp handler failed")
    finally:
        try:
            writer.close()
        except Exception:  # pragma: no cover
            pass
