"""Attribution: deciding which application, route and parameter a request proves.

The identifier in a hostname used to be enough, back when every payload template was
written against a host we own. It no longer is: a real tool points a blind payload at
its own callback host, and the name that arrives here is one we have never seen. So
attribution is a join, on three keys of decreasing strength.

1. **Registered hint** (high confidence). Just before a planted sink performs an
   outbound request whose destination came from user input, the application registers
   what it is about to do -- destination host, route, parameter, request id, signal --
   with the reporting endpoint. We ask that endpoint for the hints matching the host we
   just saw; a match is the evidence, because it pairs "the application was about to
   fetch X for parameter P" with "X was looked up from that application's container".

2. **Owned-zone label** (high confidence). The static form, for payload templates that
   do name our own zone.

3. **Source address within a time window** (low confidence). Nothing matched by host,
   but we know which container the request came from, because that address registered a
   hint recently. That says "this application made an outbound request to a host it was
   given", which is meaningful, but not which parameter caused it -- so it is reported
   as low confidence and the downstream analysis can count it separately rather than
   silently mixing it with proven pairs.

Two mechanisms feed the index, for two different reasons:

* a **targeted lookup** per unattributed observation, ``GET /v1/correlations
  ?destination_host=...``, run on a worker thread so no listener ever waits for it.
  This is the precise path and it costs one small filtered request per unknown host,
  rather than streaming the whole pending set several times a second.
* a slow **periodic listing** of all pending hints, whose only job is to keep the
  address-to-application map warm so rule 3 can work at all.

Host matching mirrors the server's: case-folded, trailing-dot tolerant, and a subdomain
relation in either direction (an intermediate resolver may prepend a label, and a tool
may register a wildcard host).
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable

log = logging.getLogger("edge_resolver.correlation")

HIGH = "high"
MEDIUM = "medium"
LOW = "low"
NONE = "none"

MODE_HINT = "hint"
MODE_OWNED_LABEL = "owned_label"
MODE_SOURCE = "source"
MODE_UNATTRIBUTED = "unattributed"

SCAN_LIMIT = 512


def normalise_host(host: str | None) -> str:
    if not host:
        return ""
    name = host.strip().strip(".").lower()
    if name.startswith("[") and "]" in name:
        return name[1 : name.index("]")]
    if name.count(":") == 1:
        name = name.split(":", 1)[0]
    return name


def hosts_match(observed: str, registered: str) -> bool:
    """Same relation the reporting endpoint applies; the two are kept in step on purpose."""
    if not observed or not registered:
        return False
    return (
        observed == registered
        or observed.endswith("." + registered)
        or registered.endswith("." + observed)
    )


@dataclass(frozen=True)
class Hint:
    """One pending outbound request an application announced.

    Field names follow the reporting endpoint's record: ``destination_host``,
    ``client_ip``, ``correlation_id``. Unknown keys are preserved by that API, so
    ``param_in`` is read when a sink supplies it and ignored when it does not.
    """

    hint_id: str
    ts: float
    app: str
    host: str
    expires_at: float
    signal: str | None = None
    route: str | None = None
    param: str | None = None
    param_in: str | None = None
    request_id: str | None = None
    source_ips: tuple[str, ...] = ()
    synthetic: bool = False

    @classmethod
    def from_payload(
        cls, payload: dict[str, Any], *, default_ttl: float, now: float | None = None
    ) -> "Hint | None":
        host = normalise_host(payload.get("destination_host") or payload.get("host"))
        if not host:
            return None
        moment = now if now is not None else time.time()
        ts = float(payload.get("registered_at") or payload.get("ts") or moment)
        expires_at = payload.get("expires_at")
        if expires_at is None:
            expires_at = ts + float(payload.get("ttl") or default_ttl)
        client_ip = payload.get("client_ip")
        return cls(
            hint_id=str(payload.get("correlation_id") or f"{host}:{ts}"),
            ts=ts,
            app=str(payload.get("app") or ""),
            host=host,
            expires_at=float(expires_at),
            signal=payload.get("signal"),
            route=payload.get("route"),
            param=payload.get("param"),
            param_in=payload.get("param_in"),
            request_id=payload.get("request_id"),
            source_ips=(str(client_ip),) if client_ip else (),
            synthetic=bool(payload.get("synthetic")),
        )

    def as_attribution(self) -> dict[str, Any]:
        return {
            "app": self.app or None,
            "route": self.route,
            "param": self.param,
            "param_in": self.param_in,
            "request_id": self.request_id,
            "signal": self.signal,
            "correlation_id": self.hint_id,
        }


@dataclass(frozen=True)
class Attribution:
    mode: str = MODE_UNATTRIBUTED
    confidence: str = NONE
    app: str | None = None
    hint: Hint | None = None
    age_ms: int | None = None

    def as_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"mode": self.mode, "app": self.app}
        if self.hint is not None:
            out.update(self.hint.as_attribution())
            out["app"] = self.app
            out["hint_age_ms"] = self.age_ms
        return out


UNATTRIBUTED = Attribution()


class CorrelationIndex:
    """Thread-safe index of known hints and of address-to-application mappings."""

    def __init__(self, *, hint_ttl: float = 120.0, source_ttl: float = 900.0, max_hints: int = 20000) -> None:
        self.hint_ttl = hint_ttl
        self.source_ttl = source_ttl
        self._lock = threading.Lock()
        self._by_host: dict[str, list[Hint]] = {}
        self._recent: deque[Hint] = deque(maxlen=max_hints)
        self._seen: dict[str, float] = {}
        self._sources: dict[str, tuple[str, float]] = {}
        self._static_sources: dict[str, str] = {}
        self.added = 0

    def set_static_sources(self, mapping: dict[str, str]) -> None:
        with self._lock:
            self._static_sources = dict(mapping)

    def add(self, hint: Hint) -> bool:
        """Index one hint. False when it is a duplicate we already hold."""
        with self._lock:
            if hint.hint_id in self._seen:
                return False
            self._seen[hint.hint_id] = hint.expires_at
            self._by_host.setdefault(hint.host, []).append(hint)
            self._recent.append(hint)
            self.added += 1
            for address in hint.source_ips:
                if hint.app:
                    self._sources[address] = (hint.app, hint.ts)
            self._prune_locked(time.time())
            return True

    def add_payloads(self, payloads: list[dict[str, Any]] | None) -> int:
        count = 0
        for payload in payloads or []:
            hint = Hint.from_payload(payload, default_ttl=self.hint_ttl)
            if hint is not None and self.add(hint):
                count += 1
        return count

    def note_source(self, address: str, app: str, ts: float | None = None) -> None:
        with self._lock:
            self._sources[address] = (app, ts if ts is not None else time.time())

    def app_for_source(self, address: str, now: float | None = None) -> str | None:
        moment = now if now is not None else time.time()
        with self._lock:
            learned = self._sources.get(address)
            if learned and moment - learned[1] <= self.source_ttl:
                return learned[0]
            return self._static_sources.get(address)

    def match(self, host: str | None, source_ip: str, now: float | None = None) -> Attribution:
        """Best attribution for one observation, by the three keys described above."""
        moment = now if now is not None else time.time()
        name = normalise_host(host)
        hint = self._match_host(name, source_ip, moment) if name else None
        if hint is not None:
            consistent = not hint.source_ips or source_ip in hint.source_ips
            return Attribution(
                mode=MODE_HINT,
                confidence=HIGH if consistent else MEDIUM,
                app=hint.app or None,
                hint=hint,
                age_ms=max(0, int((moment - hint.ts) * 1000)),
            )
        app = self.app_for_source(source_ip, moment)
        if app:
            return Attribution(mode=MODE_SOURCE, confidence=LOW, app=app)
        return UNATTRIBUTED

    def _match_host(self, name: str, source_ip: str, now: float) -> Hint | None:
        with self._lock:
            self._prune_locked(now)
            candidates = [h for h in self._by_host.get(name, []) if h.expires_at > now]
            if not candidates:
                for hint in list(self._recent)[-SCAN_LIMIT:]:
                    if hint.expires_at > now and hosts_match(name, hint.host):
                        candidates.append(hint)
            if not candidates:
                return None
            # Prefer a hint whose registering container is the one we heard from, then
            # the most recent: two applications can legitimately fetch the same host.
            candidates.sort(key=lambda h: (source_ip in h.source_ips, h.ts), reverse=True)
            return candidates[0]

    def _prune_locked(self, now: float) -> None:
        for host in [h for h, hints in self._by_host.items() if all(x.expires_at <= now for x in hints)]:
            del self._by_host[host]
        for host, hints in self._by_host.items():
            self._by_host[host] = [h for h in hints if h.expires_at > now]
        for hint_id, expires in list(self._seen.items()):
            if expires <= now:
                del self._seen[hint_id]
        for address, (_, seen) in list(self._sources.items()):
            if now - seen > self.source_ttl:
                del self._sources[address]

    def stats(self) -> dict[str, Any]:
        with self._lock:
            pending = sum(len(hints) for hints in self._by_host.values())
            return {"pending": pending, "received": self.added, "sources": len(self._sources)}


class HintPoller:
    """Slow full listing of pending hints, purely to keep the source map warm.

    Degrades quietly: the endpoint may be idle, restarting, or refusing us (its control
    routes answer 404 to anything but the allowlisted address). None of that may affect
    the listeners, so a failure just backs off."""

    def __init__(
        self,
        index: CorrelationIndex,
        fetch: Callable[[], list[dict[str, Any]] | None],
        *,
        interval: float = 5.0,
        backoff: float = 30.0,
    ) -> None:
        self.index = index
        self.fetch = fetch
        self.interval = interval
        self.backoff = backoff
        self.failures = 0
        self.polls = 0
        self.last_error: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="hint-poll", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        self._thread = None

    def poll_once(self) -> int:
        self.polls += 1
        try:
            payloads = self.fetch()
        except Exception as exc:  # noqa: BLE001 - a poller reports, it does not raise
            self.failures += 1
            self.last_error = f"{type(exc).__name__}: {exc}"
            return 0
        if payloads is None:
            self.failures += 1
            return 0
        self.failures = 0
        self.last_error = None
        return self.index.add_payloads(payloads)

    def _run(self) -> None:
        while not self._stop.is_set():
            self.poll_once()
            self._stop.wait(self.backoff if self.failures > 3 else self.interval)

    def stats(self) -> dict[str, Any]:
        return {"polls": self.polls, "failures": self.failures, "last_error": self.last_error}


class AttributionWorker:
    """Off-path worker doing one targeted hint lookup per unattributed observation.

    Why a worker and not an inline call: a listener must answer in the same time whether
    or not attribution succeeds. Several measurements are timing-based, and a resolver
    that paused for an HTTP round-trip before answering would be both a delay and a
    tell. The observation is stored immediately with what we already know; the event is
    reported once the lookup has had its brief chance to improve it.

    When the queue is full or the worker is not running, the caller emits inline. Losing
    precision is acceptable; losing the event is not.
    """

    def __init__(
        self,
        index: CorrelationIndex,
        lookup: Callable[[str], list[dict[str, Any]] | None],
        finalise: Callable[[Any, Attribution], None],
        *,
        queue_size: int = 2000,
    ) -> None:
        self.index = index
        self.lookup = lookup
        self.finalise = finalise
        self._queue: queue.Queue[tuple[Any, str, str]] = queue.Queue(maxsize=queue_size)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.processed = 0
        self.dropped = 0
        self.improved = 0

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="attribution", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        self._thread = None
        # Whatever was still queued is reported as it stands: a shutdown may cost
        # precision, it must not cost events.
        while True:
            try:
                observation, _, _ = self._queue.get_nowait()
            except queue.Empty:
                break
            try:
                self.finalise(observation, UNATTRIBUTED)
            except Exception:  # pragma: no cover
                log.exception("failed to report a queued observation")

    def enqueue(self, observation: Any, host: str, source_ip: str) -> bool:
        if self._thread is None:
            return False
        try:
            self._queue.put_nowait((observation, host, source_ip))
        except queue.Full:
            self.dropped += 1
            return False
        return True

    def drain(self, timeout: float = 5.0) -> None:
        """Wait until the queue is empty. For tests and for a clean shutdown."""
        deadline = time.monotonic() + timeout
        while not self._queue.empty() and time.monotonic() < deadline:
            time.sleep(0.01)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                observation, host, source_ip = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            self._handle(observation, host, source_ip)

    def _handle(self, observation: Any, host: str, source_ip: str) -> None:
        attribution = UNATTRIBUTED
        try:
            payloads = self.lookup(host)
            if payloads:
                self.index.add_payloads(payloads)
            attribution = self.index.match(host, source_ip)
            if attribution.mode == MODE_HINT:
                self.improved += 1
        except Exception as exc:  # noqa: BLE001 - never lose the event over a lookup
            log.debug("hint lookup for %s failed: %s", host, exc)
        finally:
            self.processed += 1
            try:
                self.finalise(observation, attribution)
            except Exception:  # pragma: no cover
                log.exception("failed to report observation")

    def stats(self) -> dict[str, Any]:
        return {
            "processed": self.processed,
            "improved": self.improved,
            "dropped": self.dropped,
            "queued": self._queue.qsize(),
        }
