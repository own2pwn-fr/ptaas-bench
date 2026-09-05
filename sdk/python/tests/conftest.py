"""In-process fake collector plus the fixtures every test builds on.

It is a real HTTP server on a real socket rather than a stubbed sender, so the tests
exercise the same code path a target does in compose: httpx, connection reuse, batch
serialisation, and the failure handling when that server misbehaves.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from ptaas_bench_sdk import BenchClient, config_from_env
from ptaas_bench_sdk._client import _reset_for_tests


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
            batch = json.loads(raw)["events"]
        except Exception:  # noqa: BLE001
            batch = []
        with collector.lock:
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
        self.batches: list[list[dict]] = []
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
    _reset_for_tests()
    yield
    _reset_for_tests()


@pytest.fixture()
def bench(collector: FakeCollector) -> BenchClient:
    """A client wired to the fake collector, flushing fast enough for tests."""
    from ptaas_bench_sdk import _client as client_module

    config = config_from_env(
        app="testapp", collector_url=collector.url, enabled=True, flush_interval=0.02
    )
    instance = BenchClient(config)
    client_module._ACTIVE = instance  # so bench.trigger()/get_bench() resolve to it
    yield instance
    instance.close(1.0)


@pytest.fixture()
def blackhole_url() -> str:
    """A listening socket that accepts connections and then answers nothing.

    Models the nastiest realistic collector failure: reachable, so no fast connection
    error, but never replying. If instrumentation were synchronous this would freeze
    the target.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(64)
    host, port = sock.getsockname()
    yield f"http://{host}:{port}"
    sock.close()
