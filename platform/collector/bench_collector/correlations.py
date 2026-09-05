"""Live registry of pending outbound-fetch hints, read by the egress sinkhole.

Why this exists: the sinkhole is the resolver for the whole target network, so it
captures a callback whatever hostname the payload used -- including the tool's own
collaborator domain. That is deliberate; a sealed network without it would score
every blind SSRF, XXE and command injection as missed by every tool, which measures
our topology rather than the tools. The cost is attribution: a lookup for
``9f2c.oast.fun`` says nothing about which route, which parameter or which planted
sink produced it. So a sink about to make an attacker-controlled outbound fetch
registers the hint here, and the sinkhole matches its observation against the set.

The registry is in memory, not in the database, because the DNS query arrives within
milliseconds of the registration: a buffered write would lose the race. The record
also travels through the event stream so a published score can be audited later.

TTL and a hard cap are not tidiness. A tool fuzzing an SSRF parameter registers one
hint per attempt, thousands per minute; an unbounded map would be a memory-exhaustion
bug reachable by the subject of the benchmark.
"""

from __future__ import annotations

import time
import uuid
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

Clock = Callable[[], float]


class CorrelationRegistry:
    def __init__(self, ttl: float, max_entries: int = 20_000, clock: Clock | None = None) -> None:
        self.ttl = ttl
        self.max_entries = max_entries
        # Wall clock rather than monotonic: the value is published to the sinkhole,
        # which compares it against the timestamps of its own observations.
        self.clock: Clock = clock or time.time
        self._entries: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self.registered = 0
        self.expired = 0
        self.overflowed = 0

    def register(self, record: dict[str, Any], ttl: float | None = None) -> dict[str, Any]:
        """Add a hint and return the stored record, stamped with id and expiry."""
        now = self.clock()
        entry = dict(record)
        # setdefault would not do: an event-shaped record carries the key explicitly
        # with a null value, and the sinkhole matches on this id.
        if not entry.get("correlation_id"):
            entry["correlation_id"] = uuid.uuid4().hex
        entry["registered_at"] = now
        entry["expires_at"] = now + float(ttl or entry.get("ttl") or self.ttl)
        self._evict(now)
        self._entries[entry["correlation_id"]] = entry
        self.registered += 1
        while len(self._entries) > self.max_entries:
            # Oldest first: a hint that has been pending longest is the least likely
            # to still be waiting for its callback.
            self._entries.popitem(last=False)
            self.overflowed += 1
        return entry

    def pending(self, destination_host: str | None = None) -> list[dict[str, Any]]:
        self._evict(self.clock())
        entries = list(self._entries.values())
        if destination_host:
            wanted = destination_host.strip().rstrip(".").lower()
            entries = [entry for entry in entries if _host_matches(entry, wanted)]
        return entries

    def get(self, correlation_id: str) -> dict[str, Any] | None:
        self._evict(self.clock())
        return self._entries.get(correlation_id)

    def discard(self, correlation_id: str) -> dict[str, Any] | None:
        return self._entries.pop(correlation_id, None)

    def _evict(self, now: float) -> None:
        expired = [key for key, entry in self._entries.items() if entry["expires_at"] <= now]
        for key in expired:
            del self._entries[key]
        self.expired += len(expired)

    def stats(self) -> dict[str, Any]:
        self._evict(self.clock())
        return {
            "pending": len(self._entries),
            "registered": self.registered,
            "expired": self.expired,
            "overflowed": self.overflowed,
            "ttl": self.ttl,
        }


def _host_matches(entry: dict[str, Any], wanted: str) -> bool:
    """Exact host, or an observed subdomain of the registered one.

    A payload pointing at ``9f2c.oast.fun`` is often resolved as
    ``_x.9f2c.oast.fun`` by an intermediate, and a tool that registers a wildcard
    collaborator would otherwise never match.
    """
    host = str(entry.get("destination_host") or "")
    return bool(host) and (wanted == host or wanted.endswith("." + host) or host.endswith("." + wanted))
