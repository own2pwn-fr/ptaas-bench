"""DNS listener: the resolver for the application network, on UDP and TCP.

It answers *every* name, in any zone, with its own address on the interface the client
can reach. That is the whole point. The requests worth recording are the ones aimed at a
host chosen by someone else -- an application talked into fetching
``http://z9x2k1p8.example-collab.net/`` is doing something notable -- and a network where
that lookup simply failed would record nothing at all, which would say more about our
topology than about the application or the client.

Being the network's only resolver also means answering the names that network really
has -- the reporting endpoint, databases, sibling services -- so two classes of name are
forwarded to the upstream resolver inside this container (Docker's embedded server at
127.0.0.11, which sees both networks because we are attached to both):

* **explicit internal names**: RESOLVER_INTERNAL_NAMES plus the reporting endpoint's own
  hostname. If the upstream cannot answer one of those, the reply is SERVFAIL rather than
  our own address: these are known infrastructure, and quietly pointing an application's
  database client at us would turn a transient resolver blip into a silent misconnection.
* **single-label names** (no dot at all), which is what a compose service name looks
  like. These are ambiguous -- a payload can use a single-label host too -- so an
  upstream miss falls through to the sinkhole answer and is recorded.

Names that resolve upstream are not recorded: they are routine infrastructure chatter,
and keeping them would bury the requests that matter. Arbitrary multi-label names are
never forwarded: that would be slow, would leak lookups off the network, and would let a
client use us as an open resolver.

A third, narrow exception: names in RESOLVER_DENYLIST get NXDOMAIN. Empty by default. A
name that does not resolve is a distinctive behaviour, so this exists only for the rare
planted defect whose condition is precisely that an outbound lookup fails.

Whatever happens, the reply is immediate. On a network with no route out, an unanswered
query costs the client its full resolver timeout, and a captured callback would then look
like an application error -- so upstream work is bounded by RESOLVER_UPSTREAM_TIMEOUT and
every path ends in an answer.

The A answer is per client (see net.local_address_towards): on a dual-homed host a fixed
answer would hand out an address half the clients cannot reach. MX queries are answered
with the queried name itself, so a mail path resolves onward to the SMTP listener
instead of dead-ending.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import struct
import time

from .. import dnswire
from ..config import Config
from ..net import local_address_towards
from ..recorder import Recorder
from ..tokens import Candidate, host_label

log = logging.getLogger("edge_resolver.dns")

TXT_ANSWER = "v=spf1 -all"
UPSTREAM_CACHE_TTL = 30.0


class DnsHandler:
    def __init__(self, config: Config, recorder: Recorder, upstream=None) -> None:
        self.config = config
        self.recorder = recorder
        self._answer_cache: dict[str, str] = {}
        self._upstream_cache: dict[str, tuple[tuple[str, ...], float]] = {}
        self._internal = config.internal_suffixes()
        self._denylist = tuple(config.denylist)
        # Injectable so the behaviour can be tested without a Docker resolver; the
        # default goes through the container's own resolver configuration.
        self._upstream = upstream

    # -- policy ---------------------------------------------------------------

    def _matches(self, name: str, patterns: tuple[str, ...]) -> bool:
        return any(name == pattern or name.endswith("." + pattern) for pattern in patterns)

    def is_internal_name(self, name: str) -> bool:
        return self._matches(name, self._internal)

    def is_single_label(self, name: str) -> bool:
        return self.config.forward_single_label and "." not in name and bool(name)

    def is_denied(self, name: str) -> bool:
        return self._matches(name, self._denylist)

    def answer_ip(self, peer_ip: str) -> str:
        if self.config.public_ip:
            return self.config.public_ip
        cached = self._answer_cache.get(peer_ip)
        if cached:
            return cached
        address = local_address_towards(peer_ip, 53, default="127.0.0.1") or "127.0.0.1"
        if len(self._answer_cache) > 1024:  # one entry per client, not per query
            self._answer_cache.clear()
        self._answer_cache[peer_ip] = address
        return address

    async def resolve_upstream(self, name: str) -> tuple[str, ...]:
        """Ask the container's own resolver. Empty tuple on miss, error or timeout.

        Positive answers are cached briefly; failures are not, so a blip does not stick
        for the cache lifetime."""
        now = time.monotonic()
        cached = self._upstream_cache.get(name)
        if cached and cached[1] > now:
            return cached[0]
        try:
            addresses = await asyncio.wait_for(
                self._resolve(name), timeout=self.config.upstream_timeout
            )
        except (asyncio.TimeoutError, TimeoutError):
            log.warning("upstream lookup for %s timed out", name)
            return ()
        except (OSError, socket.gaierror):
            return ()
        if addresses:
            self._upstream_cache[name] = (tuple(addresses), now + UPSTREAM_CACHE_TTL)
        return tuple(addresses)

    async def _resolve(self, name: str) -> tuple[str, ...]:
        if self._upstream is not None:
            return tuple(await self._upstream(name))
        loop = asyncio.get_running_loop()
        infos = await loop.getaddrinfo(
            name, None, family=socket.AF_INET, type=socket.SOCK_STREAM
        )
        return tuple(dict.fromkeys(info[4][0] for info in infos))

    # -- handling -------------------------------------------------------------

    async def handle(self, data: bytes, peer_ip: str, proto: str) -> bytes:
        try:
            query = dnswire.parse_query(data)
        except dnswire.DnsFormatError as exc:
            self.recorder.record(
                channel="dns",
                source_ip=peer_ip,
                candidates=[],
                raw=f"dns/{proto} unparseable query ({exc}): {data[:256].hex()}",
                detail={"proto": proto, "malformed": True},
                owned_zone=None,
            )
            return dnswire.build_format_error(data)

        if query.question is None:
            return dnswire.build_response(
                query, rcode=dnswire.RCODE_FORMERR, authoritative=False
            )

        question = query.question
        name = question.name
        zone = self.config.owned_zone
        owned = name == zone or name.endswith("." + zone)

        explicit_internal = self.is_internal_name(name)
        if explicit_internal or self.is_single_label(name):
            addresses = await self.resolve_upstream(name)
            if addresses:
                return self._forwarded_response(query, addresses)
            if explicit_internal:
                # Known infrastructure that the upstream could not answer. SERVFAIL is
                # immediate and lets the client retry; handing back our own address would
                # point a database client at this process instead.
                return dnswire.build_response(
                    query, rcode=dnswire.RCODE_SERVFAIL, authoritative=False
                )
            # A single-label name the upstream does not know is very likely a payload,
            # so it falls through to the sinkhole answer below and is recorded.

        if self.is_denied(name):
            self._record(query, name, peer_ip, proto, owned, "nxdomain")
            return dnswire.build_response(
                query,
                rcode=dnswire.RCODE_NXDOMAIN,
                authority=[(zone, dnswire.TYPE_SOA, dnswire.soa_rdata(zone))] if owned else None,
                ttl=self.config.dns_ttl,
            )

        address = self.answer_ip(peer_ip)
        self._record(query, name, peer_ip, proto, owned, address)

        answers: list[tuple[int, bytes]] = []
        authority: list[tuple[str, int, bytes]] = []
        qtype = question.qtype
        if question.qclass in (dnswire.CLASS_IN, dnswire.CLASS_ANY):
            if qtype in (dnswire.TYPE_A, dnswire.TYPE_ANY):
                answers.append((dnswire.TYPE_A, dnswire.a_rdata(address)))
            if qtype in (dnswire.TYPE_TXT, dnswire.TYPE_ANY):
                answers.append((dnswire.TYPE_TXT, dnswire.txt_rdata(TXT_ANSWER)))
            if qtype == dnswire.TYPE_MX:
                # Point mail at the name itself, which resolves back here by the rule
                # above: an MTA then delivers to our SMTP listener rather than giving up.
                answers.append(
                    (dnswire.TYPE_MX, struct.pack("!H", 10) + dnswire.encode_name(name))
                )
            if qtype in (dnswire.TYPE_NS, dnswire.TYPE_ANY):
                answers.append((dnswire.TYPE_NS, dnswire.encode_name(f"ns1.{zone}")))
            if qtype == dnswire.TYPE_SOA and owned:
                answers.append((dnswire.TYPE_SOA, dnswire.soa_rdata(zone)))
        if not answers and owned:
            # NOERROR with an empty answer plus the SOA: the honest reply for a name we
            # own but a type we do not serve. NXDOMAIN would make a client give up on the
            # name entirely, including for A.
            authority.append((zone, dnswire.TYPE_SOA, dnswire.soa_rdata(zone)))

        return dnswire.build_response(
            query,
            answers=answers,
            authority=authority,
            authoritative=owned,
            ttl=self.config.dns_ttl,
        )

    def _forwarded_response(self, query: dnswire.Query, addresses: tuple[str, ...]) -> bytes:
        """Answer an internal name truthfully, as a forwarding resolver would."""
        question = query.question
        assert question is not None
        answers: list[tuple[int, bytes]] = []
        if question.qtype in (dnswire.TYPE_A, dnswire.TYPE_ANY):
            answers = [(dnswire.TYPE_A, dnswire.a_rdata(address)) for address in addresses]
        return dnswire.build_response(
            query, answers=answers, authoritative=False, ttl=self.config.dns_ttl
        )

    def _record(
        self, query: dnswire.Query, name: str, peer_ip: str, proto: str, owned: bool, outcome: str
    ) -> None:
        question = query.question
        assert question is not None
        self.recorder.record(
            channel="dns",
            source_ip=peer_ip,
            host=name,
            candidates=[Candidate("dns_label", host_label(name, self.config.owned_zone) or "")],
            raw=(
                f"dns/{proto} qname={name} qtype={dnswire.type_name(question.qtype)} "
                f"qclass={question.qclass} answer={outcome}"
            ),
            detail={
                "proto": proto,
                "qname": name,
                "qtype": dnswire.type_name(question.qtype),
                "answer": outcome,
            },
            owned_zone=owned,
        )


class DnsUdpProtocol(asyncio.DatagramProtocol):
    def __init__(self, handler: DnsHandler) -> None:
        self.handler = handler
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport) -> None:  # type: ignore[override]
        self.transport = transport

    def datagram_received(self, data: bytes, addr) -> None:  # type: ignore[override]
        # A task, not an inline call: answering may require an upstream lookup, and the
        # loop must stay free for the other four listeners meanwhile.
        asyncio.get_running_loop().create_task(self._respond(data, addr))

    async def _respond(self, data: bytes, addr) -> None:
        try:
            response = await self.handler.handle(data, addr[0], "udp")
        except Exception:  # pragma: no cover - a listener must not die on one packet
            log.exception("dns/udp handling failed")
            return
        if self.transport is not None and response:
            self.transport.sendto(response, addr)

    def error_received(self, exc: Exception) -> None:  # pragma: no cover
        log.debug("dns/udp error: %s", exc)


async def handle_tcp(
    handler: DnsHandler, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> None:
    """RFC 1035 4.2.2 framing: each message is prefixed with its 16-bit length."""
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
            response = await handler.handle(payload, peer[0], "tcp")
            writer.write(len(response).to_bytes(2, "big") + response)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        raise
    except Exception:  # pragma: no cover
        log.exception("dns/tcp handling failed")
    finally:
        try:
            writer.close()
        except Exception:  # pragma: no cover
            pass
