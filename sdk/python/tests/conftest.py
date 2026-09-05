"""In-process fake collector plus the fixtures every test builds on.

It is a real HTTP server on a real socket rather than a stubbed sender, so the tests
exercise the same path a deployed target does: httpx, connection reuse, batch
serialisation, and the failure handling when that server misbehaves. It answers both
collector paths: /v1/traces (records) and /v1/correlations (outbound registrations).
"""

from __future__ import annotations

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from telemetry_agent import TelemetryClient, config_from_env
from telemetry_agent._client import _reset_active


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler naming
        length = int(self.headers.get("content-length") or 0)
        raw = self.rfile.read(length)
        collector: "FakeCollector" = self.server.collector  # type: ignore[attr-defined]
        if collector.delay:
            time.sleep(collector.delay)
        if collector.status >= 500:
            self.send_response(collector.status)
            self.send_header("content-length", "0")
            self.end_headers()
            return
        try:
            body = json.loads(raw)
        except Exception:  # noqa: BLE001
            body = {}
        with collector.lock:
            collector.paths.append(self.path)
            if self.path.rstrip("/").endswith("correlations"):
                # One dependency link per request, sent as the bare record: it is
                # dispatched the moment it is registered, so there is nothing to batch.
                collector.correlations.append(body)
            else:
                batch = body.get("events", [])
                collector.batches.append(batch)
                collector.events.extend(batch)
        self.send_response(202)
        self.send_header("content-length", "0")
        self.end_headers()

    def log_message(self, *args, **kwargs) -> None:
        return


class FakeCollector:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.events: list[dict] = []
        self.correlations: list[dict] = []
        self.batches: list[list[dict]] = []
        self.paths: list[str] = []
        self.delay = 0.0
        self.status = 202
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._server.collector = self  # type: ignore[attr-defined]
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def wait_for_correlations(self, count: int = 1, timeout: float = 5.0) -> list[dict]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self.lock:
                if len(self.correlations) >= count:
                    return list(self.correlations)
            time.sleep(0.005)
        with self.lock:
            return list(self.correlations)

    def wait_for(self, count: int, timeout: float = 5.0) -> list[dict]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self.lock:
                if len(self.events) >= count:
                    return list(self.events)
            time.sleep(0.01)
        with self.lock:
            return list(self.events)

    def of_type(self, event_type: str) -> list[dict]:
        with self.lock:
            return [e for e in self.events if e.get("type") == event_type]

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture()
def collector() -> FakeCollector:
    fake = FakeCollector()
    yield fake
    fake.close()


@pytest.fixture(autouse=True)
def _clean_singleton():
    _reset_active()
    yield
    _reset_active()


# The platform's own traffic (seeding, self-test, health checks) comes from this range;
# targets get it in TELEMETRY_SYNTHETIC_CIDRS. Requests from it are marked synthetic and
# are never scored, so the platform cannot credit a tool with its own traffic.
SYNTHETIC_CIDRS = ("10.99.0.0/16",)
SYNTHETIC_PEER = "10.99.0.7"
ORGANIC_PEER = "203.0.113.9"


@pytest.fixture()
def telemetry(collector: FakeCollector) -> TelemetryClient:
    """A client wired to the fake collector, exporting fast enough for tests."""
    from telemetry_agent import _client as client_module

    config = config_from_env(
        service="testapp",
        endpoint=collector.url,
        enabled=True,
        flush_interval=0.02,
        synthetic_cidrs=SYNTHETIC_CIDRS,
    )
    instance = TelemetryClient(config)
    client_module._ACTIVE = instance  # so get_telemetry() resolves to it
    yield instance
    instance.close(1.0)


@pytest.fixture()
def blackhole_url() -> str:
    """A listening socket that accepts connections and then answers nothing.

    Models the nastiest realistic collector failure: reachable, so no fast connection
    error, but never replying. Synchronous instrumentation would freeze the target.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(64)
    host, port = sock.getsockname()
    yield f"http://{host}:{port}"
    sock.close()
