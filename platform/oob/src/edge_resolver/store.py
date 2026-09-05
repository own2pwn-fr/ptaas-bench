"""In-memory ring of observations, plus the sequence the admin API pages on.

The reporting endpoint holds the durable record; this store exists so the platform's
own self-tests can assert "the resolver saw it" without querying a database, and so a
human debugging a deployment can watch requests land. It is bounded: a client that
hammers the listeners for an hour must not grow the process without limit.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Observation:
    """One request that reached us on any channel.

    ``token`` is None when nothing identifier-shaped was present. ``raw_token`` is the
    string as it appeared on the wire -- ``shop0031-9f2c`` for the dynamic form, or the
    best-ranked candidate when nothing parsed.

    ``host`` is the name the client was actually after (the queried name, the Host
    header, the mail domain). It is the join key: an application that told us it was
    about to fetch that host is what turns this line into evidence.

    ``owned_zone`` is True when the name sits under the zone we own, False when it is a
    name the tool chose (the common case), None where the channel carries no name at
    all (LDAP has no SNI, so we do not guess).

    Not frozen, and one field group is mutable on purpose: ``app``, ``confidence`` and
    ``attribution`` are refined by the attribution worker a few milliseconds after the
    record is created, so that a listener never waits on a lookup. Mutate only through
    ObservationStore.update, which holds the lock and wakes readers.
    """

    seq: int
    ts: float
    channel: str
    host: str | None
    token: str | None
    nonce: str | None
    source: str | None
    raw_token: str | None
    owned_zone: bool | None
    known: bool
    source_ip: str
    synthetic: bool
    app: str | None
    confidence: str
    attribution: dict[str, Any]
    raw: str
    detail: dict[str, Any] = field(default_factory=dict)

    def as_json(self) -> dict[str, Any]:
        return asdict(self)


class ObservationStore:
    """Thread-safe bounded ring. Listeners write from the event loop; the admin API and
    the tests read from other threads."""

    def __init__(self, maxlen: int = 20000) -> None:
        self._items: deque[Observation] = deque(maxlen=maxlen)
        self._cond = threading.Condition()
        self._seq = 0

    def add(self, **fields: Any) -> Observation:
        with self._cond:
            self._seq += 1
            item = Observation(seq=self._seq, ts=fields.pop("ts", None) or time.time(), **fields)
            self._items.append(item)
            self._cond.notify_all()
            return item

    def update(self, observation: Observation, **fields: Any) -> Observation:
        """Refine a stored record in place (attribution arriving after the fact)."""
        with self._cond:
            for name, value in fields.items():
                setattr(observation, name, value)
            self._cond.notify_all()
            return observation

    def since(self, seq: int = 0, limit: int = 1000) -> list[Observation]:
        with self._cond:
            return [item for item in self._items if item.seq > seq][:limit]

    def all(self) -> list[Observation]:
        with self._cond:
            return list(self._items)

    def last_seq(self) -> int:
        with self._cond:
            return self._seq

    def reset(self) -> None:
        """Drop everything and restart the sequence, so a self-test can page from 0."""
        with self._cond:
            self._items.clear()
            self._seq = 0
            self._cond.notify_all()

    def wait_for(self, count: int = 1, timeout: float = 5.0, since: int = 0) -> list[Observation]:
        """Block until ``count`` items past ``since`` exist, or the timeout expires.

        Keeps polling loops out of the callers (tests, and the admin API's long poll)."""
        deadline = time.monotonic() + timeout
        with self._cond:
            while True:
                found = [item for item in self._items if item.seq > since]
                if len(found) >= count:
                    return found
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return found
                self._cond.wait(remaining)

    def __len__(self) -> int:
        with self._cond:
            return len(self._items)
