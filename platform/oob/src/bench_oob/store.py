"""In-memory ring of observed callbacks, plus the sequence the control API pages on.

The collector is the durable record; this store exists so the platform's own
self-tests can assert "the canary saw it" without querying the collector database,
and so a human debugging a target can watch callbacks land. It is bounded: a tool
that hammers the canary for an hour must not grow the process without limit.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class Callback:
    """One observed out-of-band hit.

    ``token`` is None for an unrecognised callback (see tokens.UNKNOWN_TOKEN for what
    goes to the collector instead). ``raw_token`` is the string as it appeared on the
    wire -- ``shop0031-9f2c`` for the dynamic form, or, when nothing parsed, the
    best-ranked candidate we saw, so an unknown callback is still identifiable.
    ``in_zone`` says whether the payload named our own domain: False means the tool used
    a domain of its own and we saw it anyway; None means the channel carries no hostname
    (LDAP) and we refuse to guess.
    """

    seq: int
    ts: float
    channel: str
    token: str | None
    nonce: str | None
    source: str | None
    raw_token: str | None
    in_zone: bool
    known: bool
    source_ip: str
    raw: str
    detail: dict[str, Any] = field(default_factory=dict)

    def as_json(self) -> dict[str, Any]:
        return asdict(self)


class CallbackStore:
    """Thread-safe bounded ring. Listeners write from the event loop, the control API
    and tests read from other threads."""

    def __init__(self, maxlen: int = 5000) -> None:
        self._items: deque[Callback] = deque(maxlen=maxlen)
        self._cond = threading.Condition()
        self._seq = 0

    def add(self, **fields: Any) -> Callback:
        with self._cond:
            self._seq += 1
            callback = Callback(seq=self._seq, ts=fields.pop("ts", None) or time.time(), **fields)
            self._items.append(callback)
            self._cond.notify_all()
            return callback

    def since(self, seq: int = 0, limit: int = 1000) -> list[Callback]:
        with self._cond:
            return [c for c in self._items if c.seq > seq][:limit]

    def all(self) -> list[Callback]:
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

    def wait_for(self, count: int = 1, timeout: float = 5.0, since: int = 0) -> list[Callback]:
        """Block until ``count`` callbacks past ``since`` exist, or the timeout expires.

        Used by tests and by the control API's blocking mode; keeps polling loops out
        of the callers."""
        deadline = time.monotonic() + timeout
        with self._cond:
            while True:
                found = [c for c in self._items if c.seq > since]
                if len(found) >= count:
                    return found
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return found
                self._cond.wait(remaining)

    def __len__(self) -> int:
        with self._cond:
            return len(self._items)
