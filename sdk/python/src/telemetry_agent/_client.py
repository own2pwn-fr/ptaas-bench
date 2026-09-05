"""The exporter: a bounded in-memory queue drained by one background thread.

Everything here serves one rule: **the collector must never be observable from a served
request**. An observability agent that adds latency, or that adds latency only when the
collector is slow, corrupts the very numbers it exists to measure and turns a collector
outage into an application outage. Consequences, all intentional:

* recording is an append to an in-memory deque -- no I/O, no name resolution, no lock
  held across a syscall, and no exception allowed to escape into the caller;
* a daemon thread owns the HTTP client and exports batches of up to 500 records every
  250 ms, or as soon as a batch's worth has piled up;
* when the queue is full the *oldest* records are discarded and counted. Back-pressure
  is never applied to the application: a lost record costs a line on a dashboard, a
  blocked request costs a user;
* a collector that is down, hung or returning 500s only ever moves counters.
"""

from __future__ import annotations

import atexit
import functools
import ipaddress
import os
import re
import threading
import time
from collections import deque
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import urlsplit

from . import _context
from ._config import TelemetryConfig, config_from_env
from ._params import ParamCollector, describe_param, flatten_json, graphql_params

ATTRIBUTE_MAX = 1024

# Signal names are metric names: lower case, at least three dot-separated segments
# (service.area.condition). The pattern is the one the receiving side enforces, and it
# is enforced here as well so a malformed name fails where the author can see it rather
# than being dropped downstream, where nothing would ever say so.
SIGNAL_NAME = re.compile(r"^[a-z][a-z0-9]*(\.[a-z0-9_]+){2,}$")

# Dependency-link dispatch is separate from the record queue and is bounded on its own,
# so a burst of request-controlled destinations cannot evict queued records.
CORRELATION_QUEUE_MAX = 2048

Sender = Callable[[str, list[dict[str, Any]]], None]


class TelemetryClient:
    """Fire-and-forget exporter. One instance per process (see :func:`init_telemetry`)."""

    def __init__(self, config: TelemetryConfig | None = None, sender: Sender | None = None) -> None:
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
        # Batches taken off the queue but not yet acknowledged, so that flush() means
        # "delivered", not merely "dequeued".
        self._inflight = 0
        self._counters = {
            "enqueued": 0,
            "sent": 0,
            "dropped": 0,
            "send_failures": 0,
            "batches": 0,
            "invalid_signals": 0,
            "links_sent": 0,
            "links_dropped": 0,
            "links_failed": 0,
        }
        self._counters_lock = threading.Lock()
        self._networks: tuple[Any, ...] | None = None
        # Dependency links travel on their own thread and their own connection: the
        # lookup they explain happens microseconds later, so they cannot wait for the
        # next export tick.
        self._links: deque[dict[str, Any]] = deque()
        self._links_lock = threading.Lock()
        self._links_wake = threading.Event()
        self._links_thread: threading.Thread | None = None
        self._links_inflight = 0
        self._links_http: Any = None
        if self.config.enabled:
            atexit.register(self._atexit)

    # ------------------------------------------------------------------ recording

    def emit(self, record: dict[str, Any]) -> None:
        """Queue an already-built record. Never raises, never waits on I/O."""
        if not self.config.enabled:
            return
        try:
            self._ensure_worker()
            with self._lock:
                if len(self._queue) >= self.config.queue_max:
                    self._queue.popleft()
                    self._bump("dropped")
                self._queue.append(record)
                size = len(self._queue)
            self._bump("enqueued")
            if size >= self.config.batch_max:
                self._wake.set()
        except Exception:  # noqa: BLE001 - the agent must never fail its host process
            pass

    def _synthetic(self, synthetic: bool | None) -> bool:
        if synthetic is not None:
            return bool(synthetic)
        ctx = _context.current()
        return bool(ctx.synthetic) if ctx else False

    def _peer_ip(self) -> str:
        ctx = _context.current()
        return ctx.peer_ip if ctx else ""

    def _base(self, record_type: str, synthetic: bool | None) -> dict[str, Any]:
        return {
            "type": record_type,
            "app": self.config.service,
            "ts": time.time(),
            "synthetic": self._synthetic(synthetic),
            # The address the socket reported, and only that. The receiving end
            # classifies traffic on it, and the only place it can be observed is here:
            # by the time a record reaches the collector, the peer *it* sees is this
            # container. Empty when the address we were handed was a forwarded claim.
            "peer_ip": self._peer_ip(),
        }

    def signal(
        self,
        name: str,
        attributes: Mapping[str, Any] | None = None,
        *,
        request_id: str | None = None,
        synthetic: bool | None = None,
    ) -> None:
        """Record a named application signal with free-form attributes.

        Signals are the counters an application raises for itself: a query plan that
        came back with an unexpected row shape, a template that rendered something it
        should not have, a subject id that did not match the row it was handed. They
        are named like metrics (``shop.catalog.query.plan_anomaly``) and carry whatever
        context makes the occurrence explicable later::

            telemetry.signal("shop.catalog.query.plan_anomaly",
                             {"payload": term, "detail": "row shape outside projection"})

        Raise one on an observed effect, not on the shape of an input: a counter that
        moves whenever a request merely *looks* unusual is noise nobody can act on.

        A name that is not metric-shaped is counted and dropped rather than raised: a
        service must never fail or log because of a typo here, and a malformed name
        would create a series no chart is watching anyway.
        """
        try:
            if not SIGNAL_NAME.match(name or ""):
                self._bump("invalid_signals")
                return
            ctx = _context.current()
            record = self._base("signal", synthetic)
            record["signal"] = name
            payload: dict[str, Any] = {}
            for key, value in (attributes or {}).items():
                payload[str(key)] = _clip(value, ATTRIBUTE_MAX)
            # An explicit request id (given as an argument or inside the attributes)
            # wins: only the caller knows when a signal belongs to an earlier request.
            rid = request_id or payload.get("request_id") or (ctx.request_id if ctx else None)
            if rid:
                payload["request_id"] = rid
            if payload:
                record["attributes"] = payload
            self.emit(record)
        except Exception:  # noqa: BLE001
            pass

    def note(self, message: Any, *, synthetic: bool | None = None) -> None:
        """Free-form breadcrumb, kept beside the records of the same period."""
        try:
            record = self._base("note", synthetic)
            record["message"] = _clip(message, 4096)
            self.emit(record)
        except Exception:  # noqa: BLE001
            pass

    def outbound(
        self,
        destination: str,
        *,
        signal: str | None = None,
        param: str | None = None,
        route: str | None = None,
        request_id: str | None = None,
        synthetic: bool | None = None,
    ) -> None:
        """Register an outbound dependency call whose destination came from a request.

        Call it immediately *before* the fetch::

            telemetry.outbound(url, signal="shop.imports.fetch.external", param="source_url")

        A request-controlled destination means the resulting egress -- a name lookup, a
        connection, a hit on some third party -- appears in the network's own logs with
        nothing tying it back to the request that caused it. Registering the pairing
        beforehand is what lets the two sides be joined afterwards.

        Dispatch is immediate and on its own connection rather than through the record
        queue: the name lookup follows within microseconds, and anything that waited for
        the next export tick would arrive after the effect it explains. Immediate still
        means off the request path -- this hands the record to another thread.
        """
        try:
            ctx = _context.current()
            # Built field by field rather than from _base: a link is posted on its own
            # endpoint, one object per call, so it carries no record type.
            record: dict[str, Any] = {
                "app": self.config.service,
                "ts": time.time(),
                "synthetic": self._synthetic(synthetic),
                "peer_ip": self._peer_ip(),
                "destination_host": _hostname(destination),
            }
            if ctx is not None and ctx.client_ip:
                record["client_ip"] = ctx.client_ip
            if signal:
                if SIGNAL_NAME.match(signal):
                    record["signal"] = signal
                else:
                    self._bump("invalid_signals")
            if param:
                record["param"] = param
            resolved_route = route or (ctx.route if ctx else None)
            if resolved_route:
                record["route"] = resolved_route
            rid = request_id or (ctx.request_id if ctx else None)
            if rid:
                record["request_id"] = rid
            self._dispatch_link(record)
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------- request-shaped records

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
        peer_ip: str | None = None,
    ) -> None:
        """Export one request record. Used by the middlewares and by the helpers."""
        record = self._base("http_request", synthetic)
        if peer_ip is not None:
            record["peer_ip"] = peer_ip
        record["method"] = method
        record["route"] = route
        if path is not None:
            record["path"] = path
        if status is not None:
            record["status"] = status
        record["auth_subject"] = auth_subject
        record["client_ip"] = client_ip or ""
        record["user_agent"] = user_agent or ""
        record["params"] = list(params)
        if request_id:
            record["request_id"] = request_id
        self.emit(record)

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
        """Describe a GraphQL operation as ``in="graphql"`` inputs.

        Called from the GraphQL view. During an instrumented HTTP request the
        attributes are merged into that request's record; outside one (a subscription,
        an operation resolved later) it exports a record of its own.
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
        """Describe a WebSocket frame as ``in="websocket"`` inputs.

        JSON frames are flattened like a JSON body (``message.op``,
        ``message.args.0``) so one field of one frame can be named downstream.
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

        Only the application knows who its session belongs to, and a record without a
        subject cannot answer "who was served this?" after the fact.
        """
        ctx = _context.current()
        if ctx is not None:
            ctx.auth_subject = subject

    def bind(self, func: Callable) -> Callable:
        """Carry the in-flight request context into a callable that runs elsewhere.

        The context follows ``await``, ``asyncio.to_thread`` and the framework thread
        pools (anyio copies it into its worker), so an ordinary handler needs nothing.
        A bare ``ThreadPoolExecutor`` or ``loop.run_in_executor`` does not copy it: work
        handed to one reports as though no request were in flight, losing the request
        id, the peer and the classification of the traffic that asked for it. Wrap the
        callable and it keeps them::

            pool.submit(telemetry.bind(rebuild_index), tenant_id)

        The wrapper re-enters the same context object rather than a copy, so one bound
        callable can safely run on several workers at once.
        """
        ctx = _context.current()

        @functools.wraps(func)
        def runner(*args: Any, **kwargs: Any) -> Any:
            if ctx is None:
                return func(*args, **kwargs)
            token = _context.push(ctx)
            try:
                return func(*args, **kwargs)
            finally:
                _context.pop(token)

        return runner

    def current_request_id(self) -> str | None:
        ctx = _context.current()
        return ctx.request_id if ctx else None

    def new_param_collector(self) -> ParamCollector:
        return ParamCollector(self.config.max_params)

    # --------------------------------------------------------------- peer networks

    def is_synthetic_peer(self, peer_ip: str | None) -> bool:
        """True when the socket peer sits in one of the configured generated-traffic networks.

        The argument must be the **socket peer address** and nothing else. Never a
        forwarded header, never a framework helper that folds one in: any client can
        send ``X-Forwarded-For``, so a decision taken on it is a decision taken by the
        caller. Forwarded values are still described as ordinary request attributes --
        they are just never allowed to classify the traffic.
        """
        if not peer_ip:
            return False
        networks = self._networks
        if networks is None:
            parsed = []
            for cidr in self.config.synthetic_cidrs:
                try:
                    parsed.append(ipaddress.ip_network(cidr, strict=False))
                except ValueError:
                    continue  # a typo in configuration must not break request handling
            networks = self._networks = tuple(parsed)
        if not networks:
            return False
        try:
            address = ipaddress.ip_address(peer_ip.strip())
        except ValueError:
            return False
        return any(address in network for network in networks)

    # -------------------------------------------------------------- worker/exporter

    def _ensure_worker(self) -> None:
        # A changed pid means the process manager forked us. The child inherits a copy
        # of the deque and a lock whose owner may no longer exist, so both are rebuilt
        # and inherited records are discarded: the parent still holds its own copy and
        # will export it, and exporting twice would double-count everything.
        if self._pid != os.getpid():
            self._reinit_after_fork()
        thread = self._thread
        if thread is None or not thread.is_alive():
            with self._thread_lock:
                thread = self._thread
                if thread is None or not thread.is_alive():
                    self._thread = threading.Thread(
                        target=self._run, name="telemetry-exporter", daemon=True
                    )
                    self._thread.start()

    def _ensure_link_worker(self) -> None:
        if self._pid != os.getpid():
            self._reinit_after_fork()
        thread = self._links_thread
        if thread is None or not thread.is_alive():
            with self._thread_lock:
                thread = self._links_thread
                if thread is None or not thread.is_alive():
                    self._links_thread = threading.Thread(
                        target=self._run_links, name="telemetry-links", daemon=True
                    )
                    self._links_thread.start()

    def _dispatch_link(self, record: dict[str, Any]) -> None:
        if not self.config.enabled:
            return
        self._ensure_link_worker()
        with self._links_lock:
            if len(self._links) >= CORRELATION_QUEUE_MAX:
                self._links.popleft()
                self._bump("links_dropped")
            self._links.append(record)
        self._links_wake.set()

    def _run_links(self) -> None:
        while True:
            # No polling interval: the thread sleeps on the event and wakes the moment
            # a link is registered, which is the whole point of the separate lane.
            self._links_wake.wait(0.5)
            self._links_wake.clear()
            while True:
                with self._links_lock:
                    if not self._links:
                        break
                    record = self._links.popleft()
                    self._links_inflight += 1
                try:
                    self._post_link(record)
                finally:
                    with self._links_lock:
                        self._links_inflight -= 1
            if self._stopping.is_set():
                return

    def _post_link(self, record: dict[str, Any]) -> None:
        if self._sender is not None:
            try:
                self._sender(self.config.correlations_path, [record])
                self._bump("links_sent")
            except Exception:  # noqa: BLE001
                self._bump("links_failed")
            return
        for attempt in (0, 1):
            try:
                client = self._link_client()
                response = client.post(self.config.correlations_path, json=record)
                if response.status_code < 500:
                    self._bump("links_sent")
                    return
            except Exception:  # noqa: BLE001
                self._links_http = None
            if attempt == 0:
                time.sleep(0.02)
        self._bump("links_failed")

    def _link_client(self) -> Any:
        # A connection pool of its own: a link must not queue behind a large record
        # export that is already in flight on the shared pool.
        if self._links_http is None:
            import httpx

            self._links_http = httpx.Client(
                base_url=self.config.endpoint.rstrip("/"),
                timeout=httpx.Timeout(self.config.timeout, connect=min(2.0, self.config.timeout)),
                headers={"content-type": "application/json"},
            )
        return self._links_http

    def _reinit_after_fork(self) -> None:
        self._pid = os.getpid()
        self._queue = deque()
        self._inflight = 0
        self._links = deque()
        self._links_inflight = 0
        self._links_lock = threading.Lock()
        self._links_wake = threading.Event()
        self._links_thread = None
        self._links_http = None
        self._lock = threading.Lock()
        self._counters_lock = threading.Lock()
        self._thread_lock = threading.Lock()
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self._thread = None
        # The inherited HTTP client wraps sockets shared with the parent: dropped, not
        # closed, so the parent's connections keep working.
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
                self._sender(self.config.events_path, batch)
                self._bump("sent", len(batch))
            except Exception:  # noqa: BLE001
                self._bump("send_failures")
            return
        path, payload = self.config.events_path, {"events": batch}
        # One retry: a collector restarting between two batches is the common transient
        # failure. Beyond that the batch is discarded rather than re-queued, so an
        # unreachable collector can never make memory grow.
        for attempt in (0, 1):
            try:
                client = self._client()
                response = client.post(path, json=payload)
                if response.status_code < 500:
                    self._bump("sent", len(batch))
                    return
            except Exception:  # noqa: BLE001
                self._http = None  # rebuild the connection pool before retrying
            if attempt == 0:
                time.sleep(0.05)
        self._bump("send_failures")

    def _client(self) -> Any:
        if self._http is None:
            import httpx  # imported lazily: only the exporter thread needs it

            self._http = httpx.Client(
                base_url=self.config.endpoint.rstrip("/"),
                timeout=httpx.Timeout(self.config.timeout, connect=min(2.0, self.config.timeout)),
                headers={"content-type": "application/json"},
            )
        return self._http

    # ------------------------------------------------------------------ lifecycle

    def flush(self, timeout: float = 2.0) -> bool:
        """Wait until the queue is empty or ``timeout`` elapses. Shutdown and tests only."""
        if not self.config.enabled:
            return True
        self._ensure_worker()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self._wake.set()
            if self._idle():
                return True
            time.sleep(0.005)
        return self._idle()

    def _idle(self) -> bool:
        with self._lock:
            if self._queue or self._inflight:
                return False
        with self._links_lock:
            return not self._links and self._links_inflight == 0

    def close(self, timeout: float = 2.0) -> None:
        self._stopping.set()
        self._wake.set()
        self._links_wake.set()
        for thread in (self._thread, self._links_thread):
            if thread is not None and thread.is_alive():
                thread.join(timeout)
        for attribute in ("_http", "_links_http"):
            http = getattr(self, attribute)
            setattr(self, attribute, None)
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
        """Exporter health. ``dropped`` above zero means records were lost."""
        with self._counters_lock:
            snapshot = dict(self._counters)
        with self._lock:
            snapshot["queued"] = len(self._queue)
        with self._links_lock:
            snapshot["links_queued"] = len(self._links)
        return snapshot


# Headers through which a caller can announce an address. They are described as
# ordinary request attributes, but they never take part in a classification decision.
FORWARDED_HEADERS = ("x-forwarded-for", "x-real-ip", "forwarded", "true-client-ip", "client-ip")


def peer_matches_forwarded_claim(peer_ip: str, header_map: Mapping[str, str]) -> bool:
    """True when the caller itself announced the address we are about to classify on.

    Defence in depth against a deployment where something upstream (a proxy-header
    middleware, ProxyFix) has already replaced the peer address with a header value:
    the address then is not the socket's, it is the caller's claim, and classifying on
    it would let any caller decide how its own traffic is counted.
    """
    if not peer_ip:
        return False
    for header in FORWARDED_HEADERS:
        raw = header_map.get(header)
        if not raw:
            continue
        for chunk in raw.replace(";", ",").split(","):
            candidate = chunk.strip().strip('"')
            if "=" in candidate:  # Forwarded: for=192.0.2.1;proto=https
                candidate = candidate.split("=", 1)[1].strip().strip('"')
            candidate = candidate.strip("[]")
            if candidate.rsplit(":", 1)[0].strip("[]") == peer_ip or candidate == peer_ip:
                return True
    return False


def _clip(value: Any, limit: int) -> str:
    text = value if isinstance(value, str) else (value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value))
    return text[:limit]


def _hostname(destination: str) -> str:
    """Host part of a URL, or the value itself when it is already a host."""
    text = (destination or "").strip()
    if "//" in text:
        parsed = urlsplit(text)
        if parsed.hostname:
            return parsed.hostname
    head = text.split("/", 1)[0].split("?", 1)[0]
    if head.startswith("[") and "]" in head:  # IPv6 literal
        return head[1 : head.index("]")]
    return head.rsplit("@", 1)[-1].split(":", 1)[0]


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

_ACTIVE: TelemetryClient | None = None
_ACTIVE_LOCK = threading.Lock()


def init_telemetry(
    service: str | None = None,
    endpoint: str | None = None,
    enabled: bool | None = None,
    *,
    sender: Sender | None = None,
    **overrides: Any,
) -> TelemetryClient:
    """Create the process-wide client and return it.

    Defaults come from ``TELEMETRY_SERVICE`` / ``TELEMETRY_ENDPOINT`` /
    ``TELEMETRY_ENABLED``, so a service calls ``telemetry = init_telemetry()`` with no
    arguments and is configured entirely by its deployment.
    """
    global _ACTIVE
    config = config_from_env(service=service, endpoint=endpoint, enabled=enabled, **overrides)
    client = TelemetryClient(config, sender=sender)
    with _ACTIVE_LOCK:
        previous, _ACTIVE = _ACTIVE, client
    if previous is not None:
        previous.flush(0.5)
        previous.close(0.5)
    return client


def get_telemetry() -> TelemetryClient:
    """The active client, built from the environment on first use.

    Lazy construction matters: code that records a signal before (or without) an
    explicit ``init_telemetry`` must still record, not raise.
    """
    global _ACTIVE
    client = _ACTIVE
    if client is None:
        with _ACTIVE_LOCK:
            if _ACTIVE is None:
                _ACTIVE = TelemetryClient(config_from_env())
            client = _ACTIVE
    return client


def signal(name: str, attributes: Mapping[str, Any] | None = None, **kwargs: Any) -> None:
    """Module-level shortcut for ``get_telemetry().signal(...)``."""
    get_telemetry().signal(name, attributes, **kwargs)


def note(message: Any, **kwargs: Any) -> None:
    """Module-level shortcut for ``get_telemetry().note(...)``."""
    get_telemetry().note(message, **kwargs)


def outbound(destination: str, **kwargs: Any) -> None:
    """Module-level shortcut for ``get_telemetry().outbound(...)``."""
    get_telemetry().outbound(destination, **kwargs)


def _reset_active() -> None:
    global _ACTIVE
    with _ACTIVE_LOCK:
        previous, _ACTIVE = _ACTIVE, None
    if previous is not None:
        previous.close(0.5)
