"""The properties that make the benchmark's numbers mean anything.

A tool under test must not be able to tell an instrumented target from a plain one,
and a collector that is down, hung or crashing must change nothing the tool can
measure -- including response time, because several planted oracles are timing-based.
"""

from __future__ import annotations

import statistics
import time

from flask import Flask, jsonify
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from ptaas_bench_sdk import BenchASGIMiddleware, BenchClient, BenchWSGIMiddleware, config_from_env


async def handler(request):
    return JSONResponse({"ok": True})


ROUTES = [Route("/api/orders/{id}", handler, methods=["GET", "POST"])]


def _durations(client, count: int, **kwargs) -> list[float]:
    for _ in range(10):  # warm up connection setup and route compilation
        client.get("/api/orders/1", **kwargs)
    samples = []
    for _ in range(count):
        started = time.perf_counter()
        response = client.get("/api/orders/1", **kwargs)
        samples.append(time.perf_counter() - started)
        assert response.status_code == 200
    return samples


def test_a_hung_collector_adds_no_measurable_latency(blackhole_url):
    """The collector accepts the TCP connection and then never answers.

    Synchronous instrumentation would block here for the whole HTTP timeout on every
    request; queued instrumentation must not move the numbers at all.
    """
    bare = TestClient(Starlette(routes=ROUTES))
    client = BenchClient(config_from_env(app="t", collector_url=blackhole_url, enabled=True, timeout=30.0))
    try:
        instrumented = TestClient(BenchASGIMiddleware(Starlette(routes=ROUTES), bench=client))
        baseline = _durations(bare, 200)
        measured = _durations(instrumented, 200)

        overhead = statistics.median(measured) - statistics.median(baseline)
        assert overhead < 0.002, f"median overhead {overhead * 1000:.2f} ms"
        # The tail matters more than the median: a request that occasionally waits on
        # the collector would poison a timing oracle even if the median looked fine.
        tail = sorted(measured)[int(len(measured) * 0.99)]
        assert tail < 0.050, f"p99 {tail * 1000:.2f} ms"
        assert client.stats()["enqueued"] >= 200
    finally:
        client.close(0.2)


def test_timing_oracle_shape_is_preserved(blackhole_url):
    """A handler that sleeps 60 ms must still take ~60 ms with a dead collector."""

    async def slow(request):
        time.sleep(0.06)
        return JSONResponse({"ok": True})

    client = BenchClient(config_from_env(app="t", collector_url=blackhole_url, enabled=True, timeout=30.0))
    try:
        app = BenchASGIMiddleware(Starlette(routes=[Route("/slow", slow)]), bench=client)
        with TestClient(app) as test_client:
            samples = []
            for _ in range(10):
                started = time.perf_counter()
                test_client.get("/slow")
                samples.append(time.perf_counter() - started)
        assert 0.055 < statistics.median(samples) < 0.080
    finally:
        client.close(0.2)


def test_a_refused_collector_changes_nothing_on_the_response():
    bare = TestClient(Starlette(routes=ROUTES))
    # Port 1 on loopback: connections are refused immediately.
    client = BenchClient(config_from_env(app="t", collector_url="http://127.0.0.1:1", enabled=True))
    try:
        instrumented = TestClient(BenchASGIMiddleware(Starlette(routes=ROUTES), bench=client))
        expected = bare.get("/api/orders/1")
        actual = instrumented.get("/api/orders/1")
        assert actual.status_code == expected.status_code
        assert actual.content == expected.content
        assert dict(actual.headers) == dict(expected.headers)
        time.sleep(0.4)  # let the flusher fail a couple of times
        assert client.stats()["send_failures"] >= 1
        assert instrumented.get("/api/orders/1").status_code == 200
    finally:
        client.close(0.5)


def test_an_exception_inside_the_asgi_instrumentation_never_reaches_the_app(bench, monkeypatch):
    def explode(*args, **kwargs):
        raise RuntimeError("instrumentation is broken")

    monkeypatch.setattr(bench, "new_param_collector", explode)
    app = BenchASGIMiddleware(Starlette(routes=ROUTES), bench=bench)
    with TestClient(app) as client:
        response = client.get("/api/orders/1")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_an_exception_inside_the_wsgi_instrumentation_never_reaches_the_app(bench, monkeypatch):
    flask_app = Flask(__name__)

    @flask_app.get("/api/products")
    def products():
        return jsonify(ok=True)

    def explode(*args, **kwargs):
        raise RuntimeError("instrumentation is broken")

    monkeypatch.setattr(bench, "new_param_collector", explode)
    flask_app.wsgi_app = BenchWSGIMiddleware(flask_app.wsgi_app, bench=bench)
    response = flask_app.test_client().get("/api/products")
    assert response.status_code == 200
    assert response.get_json() == {"ok": True}


def test_application_errors_are_reported_unchanged(bench):
    async def boom(request):
        raise RuntimeError("kaboom")

    app = BenchASGIMiddleware(Starlette(routes=[Route("/boom", boom)]), bench=bench)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/boom")
    assert response.status_code == 500
    # The SDK must not smuggle anything into an error body a scanner will read.
    assert b"bench" not in response.content.lower()
    bench.flush()


def test_no_extra_routes_are_exposed(bench):
    app = BenchASGIMiddleware(Starlette(routes=ROUTES), bench=bench)
    with TestClient(app) as client:
        for path in ("/__bench", "/bench", "/_bench/events", "/healthz/bench"):
            assert client.get(path).status_code == 404
