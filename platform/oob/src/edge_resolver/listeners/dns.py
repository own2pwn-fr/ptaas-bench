"""DNS listener: the resolver for the application network, on UDP and TCP.

It answers *every* name, in any zone, with its own address on the interface the client
can reach. That is the whole point. The requests worth recording are the ones aimed at a
host chosen by someone else -- an application talked into fetching
``http://z9x2k1p8.example-collab.net/`` is doing something notable -- and a network where
that lookup simply failed would record nothing at all, which would say more about our
topology than about the application or the client.

Being the network's only resolver also means answering the names that network really has:
the reporting endpoint, databases, sibling services, and -- the case that forces the
design -- the hostnames the applications advertise for their fictional companies, which
are generated per deployment and cannot be listed anywhere in advance.

So the order is: **ask the upstream resolver first, sinkhole only what nobody claims.**
The upstream (Docker's embedded server, which sees both networks because we are attached
to both) knows every service name and every alias in the project, including the generated
ones, and answers them in about a millisecond. It has never heard of a callback domain,
so those fall through to us. That test maintains itself; an allowlist would be stale the
moment anything was reseeded, and every application would become unreachable under the
name it advertises -- which would take the whole corpus down at once.

Two refinements around it:

* **RESOLVER_INTERNAL_NAMES** (plus the reporting endpoint's hostname) marks names whose
  failure must not be papered over. If the upstream cannot answer one of those, the reply
  is SERVFAIL rather than our own address: quietly pointing a database client at this
  process would turn a transient resolver blip into a silent misconnection.
* **RESOLVER_DENYLIST** is the only path that returns NXDOMAIN. Empty by default. A name
  that does not resolve is a distinctive behaviour, so it exists only for the rare planted
  defect whose condition is precisely that an outbound lookup fails.

Names the upstream claims are answered from its reply and are not recorded: routine
infrastructure and application traffic would bury the requests that matter.

Whatever happens, the reply is immediate. On a network with no route out, an unknown name
does not come back NXDOMAIN from the upstream, it hangs -- so the question is capped at
RESOLVER_UPSTREAM_TIMEOUT (150 ms by default; a claimed name comes back in about one) and
a cap that expires simply means "unclaimed". Every path ends in an answer.

One honest limit: the claim question is always an A query, whatever the client asked. A
claimed name answered for a type we do not forward (AAAA, MX, SRV) therefore gets an
empty NOERROR rather than a forwarded answer. Every client falls back to A, and answering
those types ourselves would mean sinkholing a name somebody else owns.

The A answer is per client (see net.local_address_towards): on a dual-homed host a fixed
answer would hand out an address half the clients cannot reach. MX queries are answered
with the queried name itself, so a mail path resolves onward to the SMTP listener
instead of dead-ending.
"""

from __future__ import annotations

import asyncio
import logging
import struct

from .. import dnswire
from ..config import Config
from ..net import local_address_towards
from ..recorder import Recorder
from ..tokens import Candidate, host_label

log = logging.getLogger("edge_resolver.dns")

TXT_ANSWER = "v=spf1 -all"


class DnsHandler:
    def __init__(self, config: Config, recorder: Recorder, upstream=None) -> None:
        self.config = config
        self.recorder = recorder
        self._answer_cache: dict[str, str] = {}
        self._internal = config.internal_suffixes()
        self._denylist = tuple(config.denylist)
        # An upstream.StubResolver, or None to sinkhole everything (which is what the
        # unit tests of the serving side want).
        self.upstream = upstream

    # -- policy ---------------------------------------------------------------

    def _matches(self, name: str, patterns: tuple[str, ...]) -> bool:
        return any(name == pattern or name.endswith("." + pattern) for pattern in patterns)

    def is_internal_name(self, name: str) -> bool:
        return self._matches(name, self._internal)

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

        if self.is_denied(name):
            self._record(query, name, peer_ip, proto, owned, "nxdomain")
            return dnswire.build_response(
                query,
                rcode=dnswire.RCODE_NXDOMAIN,
                authority=[(zone, dnswire.TYPE_SOA, dnswire.soa_rdata(zone))] if owned else None,
                ttl=self.config.dns_ttl,
            )

        explicit_internal = self.is_internal_name(name)
        if self.upstream is not None:
            claim = await self.upstream.claim(name, critical=explicit_internal)
            if claim.claimed:
                return self._forwarded_response(query, claim.addresses)
            if explicit_internal:
                log.warning("upstream did not answer %s (%s)", name, claim.reason)
                return dnswire.build_response(
                    query, rcode=dnswire.RCODE_SERVFAIL, authoritative=False
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
        """Answer a claimed name from the upstream's reply, as a forwarder would."""
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
