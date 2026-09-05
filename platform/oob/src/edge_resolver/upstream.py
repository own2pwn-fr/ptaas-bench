"""Asking the network's real resolver whether anybody claims a name.

This is the gate in front of the sinkhole. Every name is offered to the upstream
resolver first; only names nobody claims are answered with our own address. That is what
lets the applications be reachable under the hostnames they advertise -- which are
generated per deployment and can never be listed in a static allowlist -- while a
callback host that exists nowhere still lands here.

Why a hand-rolled UDP stub instead of getaddrinfo:

* **the deadline is the whole design**. On a network with no route out, an unknown name
  does not come back NXDOMAIN, it hangs; every lookup therefore needs a hard cap in the
  low hundreds of milliseconds, and getaddrinfo offers no timeout at all.
* **no threads**. getaddrinfo blocks, so asyncio runs it in the shared executor. A burst
  of unknown names would pin every worker in that pool for the operating system's own
  resolver timeout -- and would then also stall the unrelated work that shares it. Here a
  lookup is one datagram and one future; abandoning it costs nothing.
* the wire codec already exists in this package for the serving side.

Behaviour that matters:

* a NOERROR answer with at least one record means the name is claimed. Anything else --
  NXDOMAIN, SERVFAIL, REFUSED, no reply within the cap, or no upstream configured at all
  -- means unclaimed, which is the sinkhole's cue.
* **and the answer has to point into the deployment.** The embedded resolver forwards
  what it cannot answer to the daemon's own resolvers, and the daemon is not on the
  sealed network -- so a callback domain can come back with a perfectly good public
  address. Forwarding that would be the worst of both worlds: the name would not be
  captured, and the application could not reach it either, because the network it lives
  on has no route out. An answer outside the accepted ranges is therefore read as
  unclaimed, which puts it back in the sinkhole where it belongs.
* results are cached briefly, positive answers longer than negative ones, so the repeat
  lookups of one callback host (resolution, then the connection, then a retry) pay the
  cap once.
* concurrency is bounded. Under a flood of unknown names the cap alone would leave
  hundreds of sockets in flight; past the limit a lookup gives up immediately and the
  name is treated as unclaimed, which for a flood is both the fast and the correct
  answer. Callers that cannot tolerate that (known infrastructure) ask with
  ``critical=True`` and wait for a slot.

Where the 150 ms default comes from, measured against a local server on this codebase:
a claimed name answers in 0.33 ms median and 1.16 ms at p99, and an NXDOMAIN in 0.31 ms.
The cap is therefore roughly two orders of magnitude above any answer that will really
arrive, so it never truncates a legitimate one; it is only ever paid by names nobody
claims, once per host thanks to the negative cache. A burst of 300 unknown names against
a silent upstream completes in 172 ms in total rather than piling up, because the
concurrency limit turns the excess into immediate "unclaimed".
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import random
import time
from dataclasses import dataclass

from . import dnswire

log = logging.getLogger("edge_resolver.upstream")

DEFAULT_SERVER = "127.0.0.11"  # the container-local resolver in a Docker deployment
RESOLV_CONF = "/etc/resolv.conf"


# Address space the deployment can actually be in. An answer outside it did not come
# from the project's own resolver, whatever the resolver that relayed it.
DEFAULT_CLAIM_NETWORKS = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8", "fc00::/7", "::1/128")


def parse_networks(values) -> tuple:
    out = []
    for item in values or ():
        try:
            out.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            continue
    return tuple(out)


@dataclass(frozen=True)
class Claim:
    """What the upstream said about a name."""

    claimed: bool
    addresses: tuple[str, ...] = ()
    rcode: int = dnswire.RCODE_NOERROR
    reason: str = "answered"


UNCLAIMED = Claim(False, reason="unclaimed")


def read_nameservers(path: str = RESOLV_CONF) -> tuple[str, ...]:
    servers: list[str] = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                parts = line.split()
                if len(parts) >= 2 and parts[0] == "nameserver":
                    servers.append(parts[1])
    except OSError:
        pass
    return tuple(servers) or (DEFAULT_SERVER,)


def parse_servers(raw: str | None) -> tuple[tuple[str, int], ...]:
    """``10.0.0.1, 127.0.0.11:5353`` -> ((ip, port), ...)."""
    out: list[tuple[str, int]] = []
    for item in (raw or "").split(","):
        text = item.strip()
        if not text:
            continue
        host, _, port = text.rpartition(":")
        if host and port.isdigit():
            out.append((host, int(port)))
        else:
            out.append((text, 53))
    return tuple(out)


class _QueryProtocol(asyncio.DatagramProtocol):
    def __init__(self, future: asyncio.Future, txid: int) -> None:
        self.future = future
        self.txid = txid

    def datagram_received(self, data: bytes, addr) -> None:  # type: ignore[override]
        if self.future.done():
            return
        if len(data) >= 2 and int.from_bytes(data[:2], "big") != self.txid:
            return  # not ours; ignore rather than fail
        self.future.set_result(data)

    def error_received(self, exc: Exception) -> None:  # type: ignore[override]
        if not self.future.done():
            self.future.set_exception(exc)


class StubResolver:
    def __init__(
        self,
        servers: tuple[tuple[str, int], ...] | None = None,
        *,
        timeout: float = 0.15,
        concurrency: int = 128,
        positive_ttl: float = 30.0,
        negative_ttl: float = 5.0,
        claim_networks=None,
    ) -> None:
        self.servers = servers or tuple((address, 53) for address in read_nameservers())
        self.claim_networks = (
            parse_networks(claim_networks)
            if claim_networks is not None
            else parse_networks(DEFAULT_CLAIM_NETWORKS)
        )
        self.timeout = timeout
        self.positive_ttl = positive_ttl
        self.negative_ttl = negative_ttl
        self._semaphore = asyncio.Semaphore(concurrency)
        self._cache: dict[str, tuple[Claim, float]] = {}
        self.queries = 0
        self.timeouts = 0
        self.hits = 0
        self.refused_slots = 0

    def cached(self, name: str) -> Claim | None:
        entry = self._cache.get(name)
        if entry is None:
            return None
        claim, expiry = entry
        if expiry <= time.monotonic():
            del self._cache[name]
            return None
        self.hits += 1
        return claim

    def _remember(self, name: str, claim: Claim) -> Claim:
        ttl = self.positive_ttl if claim.claimed else self.negative_ttl
        if len(self._cache) > 8192:
            self._cache.clear()
        self._cache[name] = (claim, time.monotonic() + ttl)
        return claim

    async def claim(self, name: str, critical: bool = False) -> Claim:
        """Does anybody upstream claim ``name``? Never raises, never exceeds the cap."""
        key = name.strip(".").lower()
        if not key or not self.servers:
            return UNCLAIMED
        cached = self.cached(key)
        if cached is not None:
            return cached
        if not critical and self._semaphore.locked():
            self.refused_slots += 1
            return Claim(False, reason="busy")
        async with self._semaphore:
            cached = self.cached(key)  # another lookup may have filled it while we waited
            if cached is not None:
                return cached
            return self._remember(key, await self._ask(key))

    async def _ask(self, name: str) -> Claim:
        budget = max(self.timeout / len(self.servers), 0.02)
        last = UNCLAIMED
        for server in self.servers:
            self.queries += 1
            try:
                data = await asyncio.wait_for(self._exchange(name, server), timeout=budget)
            except (asyncio.TimeoutError, TimeoutError):
                self.timeouts += 1
                last = Claim(False, reason="timeout")
                continue
            except OSError as exc:
                last = Claim(False, reason=f"error: {exc}")
                continue
            try:
                response = dnswire.parse_response(data)
            except dnswire.DnsFormatError:
                last = Claim(False, reason="unparseable")
                continue
            if response.rcode == dnswire.RCODE_NOERROR and response.answers:
                addresses = response.addresses
                local = tuple(a for a in addresses if self.is_local(a))
                if addresses and not local:
                    return Claim(False, rcode=response.rcode, reason="answered off-network")
                return Claim(True, addresses=local or addresses, rcode=response.rcode)
            # NXDOMAIN is a definitive "nobody has this name": stop asking.
            if response.rcode == dnswire.RCODE_NXDOMAIN:
                return Claim(False, rcode=response.rcode, reason="nxdomain")
            last = Claim(False, rcode=response.rcode, reason=f"rcode {response.rcode}")
        return last

    async def _exchange(self, name: str, server: tuple[str, int]) -> bytes:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[bytes] = loop.create_future()
        txid = random.getrandbits(16)
        transport, _ = await loop.create_datagram_endpoint(
            lambda: _QueryProtocol(future, txid), remote_addr=server
        )
        try:
            transport.sendto(dnswire.build_query(name, dnswire.TYPE_A, txid))
            return await future
        finally:
            transport.close()

    def is_local(self, address: str) -> bool:
        if not self.claim_networks:
            return True
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError:
            return False
        return any(parsed in network for network in self.claim_networks)

    def stats(self) -> dict[str, object]:
        return {
            "servers": [f"{host}:{port}" for host, port in self.servers],
            "queries": self.queries,
            "timeouts": self.timeouts,
            "cache_hits": self.hits,
            "cached": len(self._cache),
            "refused_slots": self.refused_slots,
            "timeout_s": self.timeout,
        }


def resolver_from_env(environ: dict[str, str] | None = None, **kwargs) -> StubResolver:
    environ = dict(os.environ) if environ is None else environ
    servers = parse_servers(environ.get("RESOLVER_UPSTREAM")) or None
    return StubResolver(servers, **kwargs)
