"""The bench client: a bounded queue plus one background flusher thread.

Everything in this module exists to satisfy a single hard rule: **the collector must be
invisible to the target**. Several planted oracles are timing-based (blind time-based
SQLi, timing side channels), so if instrumentation added even a few milliseconds --
worse, a variable few milliseconds when the collector is slow -- the benchmark would be
measuring the SDK instead of the tool. Consequences, all deliberate:

* ``emit`` only appends to an in-memory deque. No I/O, no DNS, no locks held across a
  syscall, no exception escaping to the application.
* A daemon thread owns the HTTP client and flushes batches of up to 500 events every
  250 ms, or immediately once a batch's worth has piled up.
* When the queue is full the *oldest* events are dropped and counted. Backpressure is
  never applied to the app: a dropped event costs a bit of ground truth, a blocked
  request would corrupt every timing measurement in the run.
* A collector that is down, hung or returning 500s only ever moves counters.
"""

from __future__ import annotations

import atexit
import os
import threading
import time
from collections import deque
from typing import Any, Callable, Iterable, Mapping

from . import _context
from ._config import BenchConfig, config_from_env
from ._params import ParamCollector, describe_param, flatten_json, graphql_params

EVIDENCE_MAX = 1024

Sender = Callable[[list[dict[str, Any]]], None]


class BenchClient:
    """Fire-and-forget event emitter. One instance per process (see :func:`init_bench`)."""

    def __init__(self, config: BenchConfig | None = None, sender: Sender | None = None) -> None:
        self.config = config or config_from_env()
        self._queue: deque[dict[str, Any]] = deque()
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self._thread: threading.Thread | None = None
        self._thread_lock = threading.Lock()
        self._http: Any = None
        self._sender = sender
        self._pid = os.getpid()
        # Batches taken off the queue but not yet acknowledged by the collector. Only
        # flush() reads it, so that "flushed" means "delivered", not "dequeued".
        self._inflight = 0
        self._counters = {"enqueued": 0, "sent": 0, "dropped": 0, "send_failures": 0, "batches": 0}
        self._counters_lock = threading.Lock()
        if self.config.enabled:
            atexit.register(self._atexit)

    # ------------------------------------------------------------------ emission

    def emit(self, event: dict[str, Any]) -> None:
        """Queue a pre-built event. Never raises, never blocks on I/O."""
        if not self.config.enabled:
            return
        try:
            self._ensure_worker()
            with self._lock:
                if len(self._queue) >= self.config.queue_max:
                    self._queue.popleft()
                    self._bump("dropped")
                self._queue.append(event)
                size = len(self._queue)
            self._bump("enqueued")
            if size >= self.config.batch_max:
                self._wake.set()
        except Exception:  # noqa: BLE001 - instrumentation must never fail the target
            pass

    def _base(self, event_type: str, synthetic: bool | None) -> dict[str, Any]:
        ctx = _context.current()
        if synthetic is None:
            synthetic = bool(ctx.synthetic) if ctx else False
        return {
            "type": event_type,
            "app": self.config.app,
            "ts": time.time(),
            "synthetic": bool(synthetic),
        }

    def trigger(
        self,
        vuln_id: str,
        *,
        oracle_kind: str | None = None,
        payload: Any = None,
        detail: Any = None,
        evidence: Mapping[str, Any] | None = None,
        request_id: str | None = None,
        synthetic: bool | None = None,
    ) -> None:
        """Report that a planted vulnerability actually fired.

        Called from inside the vulnerable sink itself, which is why the signature is
        flat and keyword-only: ``bench.trigger("BENCH-SHOP-0001", oracle_kind="sink",
        payload=sql)`` must read as one greppable line in the target's source.

        The vuln id is not validated here on purpose -- a target must never crash or
        log because of a typo in instrumentation; the collector counts malformed ids.
        """
        try:
            ctx = _context.current()
            event = self._base("trigger", synthetic)
            event["vuln_id"] = vuln_id
            if oracle_kind:
                event["oracle_kind"] = oracle_kind
            body: dict[str, Any] = dict(evidence or {})
            if payload is not None:
                body["payload"] = _clip(payload, EVIDENCE_MAX)
            if detail is not None:
                body["detail"] = _clip(detail, EVIDENCE_MAX)
            rid = request_id or (ctx.request_id if ctx else None)
            if rid:
                body["request_id"] = rid
            if body:
                event["evidence"] = body
            self.emit(event)
        except Exception:  # noqa: BLE001
            pass

    def note(self, message: Any, *, synthetic: bool | None = None) -> None:
        """Free-form breadcrumb, stored with the run for humans reading the report."""
        try:
            event = self._base("note", synthetic)
            event["message"] = _clip(message, 4096)
            self.emit(event)
        except Exception:  # noqa: BLE001
            pass

    def oob(self, token: str, channel: str, *, source_ip: str | None = None, raw: Any = None) -> None:
        """Report an out-of-band callback. Normally the canary service's job; exposed
        here for targets that host their own callback listener."""
        try:
            event = self._base("oob", None)
            event["token"] = token
            event["channel"] = channel
            if source_ip:
                event["source_ip"] = source_ip
            if raw is not None:
                event["raw"] = _clip(raw, 2048)
            self.emit(event)
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------- request-shaped events

    def record_request(
        self,
        *,
        method: str,
        route: str,
        path: str | None = None,
        status: int | None = None,
        params: Iterable[dict[str, Any]] = (),
        auth_subject: str | None = None,
        client_ip: str | None = None,
        user_agent: str | None = None,
        request_id: str | None = None,
        synthetic: bool = False,
    ) -> None:
        """Emit one ``http_request`` event. Used by the middlewares and the helpers."""
        event = self._base("http_request", synthetic)
        event["method"] = method
        event["route"] = route
        if path is not None:
            event["path"] = path
        if status is not None:
            event["status"] = status
        event["auth_subject"] = auth_subject
        event["client_ip"] = client_ip or ""
        event["user_agent"] = user_agent or ""
        event["params"] = list(params)
        if request_id:
            # Extra field (the collector preserves unknown keys): it is what makes
            # TriggerEvent.evidence.request_id resolvable back to a request.
            event["request_id"] = request_id
        self.emit(event)

    def graphql(
        self,
        query: str | None = None,
        *,
        variables: Any = None,
        operation_name: str | None = None,
        route: str = "/graphql",
        method: str = "POST",
        path: str | None = None,
        synthetic: bool | None = None,
    ) -> None:
        """Report a GraphQL operation as ``in="graphql"`` inputs.

        Call it from the GraphQL view. During an instrumented HTTP request the params
        are merged into that request's single event; outside one (subscriptions, a
        batched operation resolved later) it emits its own ``http_request`` event.
        """
        try:
            entries = [
                describe_param(name, "graphql", value)
                for name, value in graphql_params(query, variables, operation_name)
            ]
            self._attach_or_emit(entries, route=route, method=method, path=path, synthetic=synthetic)
        except Exception:  # noqa: BLE001
            pass

    def websocket(
        self,
        message: Any = None,
        *,
        route: str = "/ws",
        fields: Mapping[str, Any] | None = None,
        prefix: str = "message",
        path: str | None = None,
        synthetic: bool | None = None,
    ) -> None:
        """Report a WebSocket frame's contents as ``in="websocket"`` inputs.

        JSON frames are flattened like a JSON body (``message.op``, ``message.args.0``)
        so a catalog entry can point at one field of one frame.
        """
        try:
            pairs: list[tuple[str, str]] = []
            decoded = _maybe_json(message)
            if isinstance(decoded, (dict, list)):
                pairs.extend((f"{prefix}.{name}" if name else prefix, value) for name, value in flatten_json(decoded))
            elif message is not None:
                pairs.append((prefix, message if isinstance(message, (str, bytes)) else str(message)))
            if fields:
                pairs.extend((str(k), v if isinstance(v, (str, bytes)) else str(v)) for k, v in fields.items())
            entries = [describe_param(name, "websocket", value) for name, value in pairs]
            self._attach_or_emit(entries, route=route, method="WEBSOCKET", path=path, synthetic=synthetic)
        except Exception:  # noqa: BLE001
            pass

    def _attach_or_emit(
        self,
        entries: list[dict[str, Any]],
        *,
        route: str,
        method: str,
        path: str | None,
        synthetic: bool | None,
    ) -> None:
        ctx = _context.current()
        if ctx is not None:
            ctx.extra_params.extend(entries)
            return
        if not entries:
            return
        self.record_request(
            method=method,
            route=route,
            path=path or route,
            params=entries,
            synthetic=bool(synthetic),
        )

    def set_auth_subject(self, subject: str | None) -> None:
        """Declare the authenticated principal of the in-flight request.

        Access-control oracles (BOLA, tenant isolation) are scored on *who* was
        authenticated when another tenant's row was served, and only the app knows.
        """
        ctx = _context.current()
        if ctx is not None:
            ctx.auth_subject = subject

    def current_request_id(self) -> str | None:
        ctx = _context.current()
        return ctx.request_id if ctx else None

    def new_param_collector(self) -> ParamCollector:
        return ParamCollector(self.config.max_params)

    # -------------------------------------------------------------- worker/flusher

    def _ensure_worker(self) -> None:
        # A pid change means gunicorn/uvicorn forked us. The child inherits a copy of
        # the deque and a lock whose owner may not exist any more, so both are rebuilt
        # and inherited events are dropped: the parent still holds (and will send) its
        # own copy, and duplicated events would double-count in the scorer.
        if self._pid != os.getpid():
            self._reinit_after_fork()
        thread = self._thread
        if thread is None or not thread.is_alive():
            with self._thread_lock:
                thread = self._thread
                if thread is None or not thread.is_alive():
                    self._thread = threading.Thread(
                        target=self._run, name="ptaas-bench-flusher", daemon=True
                    )
                    self._thread.start()

    def _reinit_after_fork(self) -> None:
        self._pid = os.getpid()
        self._queue = deque()
        self._inflight = 0
        self._lock = threading.Lock()
        self._counters_lock = threading.Lock()
        self._thread_lock = threading.Lock()
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self._thread = None
        # The inherited httpx client wraps sockets shared with the parent; dropped, not
        # closed, so the parent's connections stay usable.
        self._http = None

    def _run(self) -> None:
        while True:
            self._wake.wait(self.config.flush_interval)
            self._wake.clear()
            self._drain_all()
            if self._stopping.is_set():
                self._drain_all()
                return

    def _drain_all(self) -> None:
        while True:
            batch = self._take(self.config.batch_max)
            if not batch:
                return
            try:
                self._send(batch)
            finally:
                with self._lock:
                    self._inflight -= 1

    def _take(self, limit: int) -> list[dict[str, Any]]:
        with self._lock:
            count = min(limit, len(self._queue))
            if not count:
                return []
            self._inflight += 1
            return [self._queue.popleft() for _ in range(count)]

    def _send(self, batch: list[dict[str, Any]]) -> None:
        self._bump("batches")
        if self._sender is not None:
            try:
                self._sender(batch)
                self._bump("sent", len(batch))
            except Exception:  # noqa: BLE001
                self._bump("send_failures")
            return
        payload = {"events": batch}
        # One retry: a collector restarting between two batches is the common transient
        # failure and losing those events would show up as missing ground truth. Beyond
        # that the batch is dropped rather than re-queued, so a durably dead collector
        # cannot make the queue grow without bound.
        for attempt in (0, 1):
            try:
                client = self._client()
                response = client.post("/v1/events", json=payload)
                if response.status_code < 500:
                    self._bump("sent", len(batch))
                    return
            except Exception:  # noqa: BLE001
                self._http = None  # force a fresh connection pool on retry
            if attempt == 0:
                time.sleep(0.05)
        self._bump("send_failures")

    def _client(self) -> Any:
        if self._http is None:
            import httpx  # imported lazily: only the flusher thread ever needs it

            self._http = httpx.Client(
                base_url=self.config.collector_url.rstrip("/"),
                timeout=httpx.Timeout(self.config.timeout, connect=min(2.0, self.config.timeout)),
                headers={"content-type": "application/json"},
            )
        return self._http

    # ------------------------------------------------------------------ lifecycle

    def flush(self, timeout: float = 2.0) -> bool:
        """Block until the queue is empty or ``timeout`` elapses. Tests and shutdown only."""
        if not self.config.enabled:
            return True
        self._ensure_worker()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self._wake.set()
            with self._lock:
                if not self._queue and self._inflight == 0:
                    return True
            time.sleep(0.005)
        with self._lock:
            return not self._queue and self._inflight == 0

    def close(self, timeout: float = 2.0) -> None:
        self._stopping.set()
        self._wake.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout)
        http = self._http
        self._http = None
        if http is not None:
            try:
                http.close()
            except Exception:  # noqa: BLE001
                pass

    def _atexit(self) -> None:
        if self._pid != os.getpid():
            return
        try:
            self.flush(1.0)
            self.close(1.0)
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------- counters

    def _bump(self, key: str, amount: int = 1) -> None:
        with self._counters_lock:
            self._counters[key] += amount

    def stats(self) -> dict[str, int]:
        """Queue health. ``dropped`` > 0 means ground truth was lost for this run."""
        with self._counters_lock:
            snapshot = dict(self._counters)
        with self._lock:
            snapshot["queued"] = len(self._queue)
        return snapshot


def _clip(value: Any, limit: int) -> str:
    text = value if isinstance(value, str) else (value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value))
    return text[:limit]


def _maybe_json(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, (str, bytes)):
        stripped = value.strip()
        if stripped[:1] in ("{", "[", b"{", b"["):
            import json

            try:
                return json.loads(stripped)
            except (ValueError, UnicodeDecodeError):
                return None
    return None


# --------------------------------------------------------------------- singleton

_ACTIVE: BenchClient | None = None
_ACTIVE_LOCK = threading.Lock()


def init_bench(
    app: str | None = None,
    collector_url: str | None = None,
    enabled: bool | None = None,
    *,
    sender: Sender | None = None,
    **overrides: Any,
) -> BenchClient:
    """Create the process-wide client and return it.

    Defaults come from ``BENCH_APP`` / ``BENCH_COLLECTOR_URL`` / ``BENCH_ENABLED`` so a
    target app can call ``bench = init_bench()`` with no arguments and be configured
    entirely by compose.
    """
    global _ACTIVE
    config = config_from_env(app=app, collector_url=collector_url, enabled=enabled, **overrides)
    client = BenchClient(config, sender=sender)
    with _ACTIVE_LOCK:
        previous, _ACTIVE = _ACTIVE, client
    if previous is not None:
        previous.flush(0.5)
        previous.close(0.5)
    return client


def get_bench() -> BenchClient:
    """The active client, auto-initialised from the environment on first use.

    Auto-initialisation matters: a planted sink calling ``trigger`` before (or without)
    an explicit ``init_bench`` must still report, not raise.
    """
    global _ACTIVE
    client = _ACTIVE
    if client is None:
        with _ACTIVE_LOCK:
            if _ACTIVE is None:
                _ACTIVE = BenchClient(config_from_env())
            client = _ACTIVE
    return client


def trigger(vuln_id: str, **kwargs: Any) -> None:
    """Module-level shortcut for ``get_bench().trigger(...)``."""
    get_bench().trigger(vuln_id, **kwargs)


def note(message: Any, **kwargs: Any) -> None:
    """Module-level shortcut for ``get_bench().note(...)``."""
    get_bench().note(message, **kwargs)


def _reset_for_tests() -> None:
    global _ACTIVE
    with _ACTIVE_LOCK:
        previous, _ACTIVE = _ACTIVE, None
    if previous is not None:
        previous.close(0.5)
