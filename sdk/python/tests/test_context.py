"""Context propagation: a signal raised deep in a handler must inherit its request.

This is a scoring-integrity property, not an ergonomic one. The platform's self-test
replays every catalogue PoC against the target from an address inside
TELEMETRY_SYNTHETIC_CIDRS. If a signal raised inside those replays failed to inherit
the synthetic marker, the platform's own proof that a vulnerability works would be
stored as a genuine exploitation and credited to whichever tool's run happened to be
open -- every planted flaw would look exploited by everyone, and the whole comparison
would be worthless.

The sinkhole correlations registered by outbound() carry exactly the same risk: an
unmarked one from a self-test replay would attribute the platform's own callback to
the tool under test.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading

import httpx
import pytest
from flask import Flask, jsonify
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient
from werkzeug.test import EnvironBuilder

from conftest import ORGANIC_PEER, SYNTHETIC_PEER
from telemetry_agent import TelemetryASGIMiddleware, TelemetryWSGIMiddleware, get_telemetry

SIGNAL = "shop.catalog.query.plan_anomaly"
DESTINATION = "http://9f2c.oast.fun/x"


# Three frames deep, the way a planted sink sits inside a repository inside a service
# layer: none of them was handed the request object.
def level_three() -> None:
    get_telemetry().signal(SIGNAL, {"payload": "deep"})
    get_telemetry().outbound(DESTINATION, signal="shop.imports.fetch.external", param="source_url")


def level_two() -> None:
    level_three()


def level_one() -> None:
    level_two()


def records_of(events, event_type: str):
    return [event for event in events if event.get("type") == event_type]


def drive(app, peer: str, path: str = "/sink") -> None:
    with TestClient(app, client=(peer, 51000)) as client:
        assert client.get(path).status_code == 200


def build_asgi(telemetry, routes):
    return TelemetryASGIMiddleware(Starlette(routes=routes), telemetry=telemetry)


@pytest.mark.parametrize("peer,expected", [(SYNTHETIC_PEER, True), (ORGANIC_PEER, False)])
def test_a_signal_raised_after_an_await_inherits_the_request(telemetry, collector, peer, expected):
    async def handler(request):
        await asyncio.sleep(0.01)  # the context must survive the suspension
        level_one()
        return JSONResponse({"ok": True})

    drive(build_asgi(telemetry, [Route("/sink", handler)]), peer)
    telemetry.flush()
    signal = records_of(collector.wait_for(2), "signal")[0]
    request = records_of(collector.events, "http_request")[0]
    correlation = collector.wait_for_correlations()[0]

    assert signal["synthetic"] is expected
    assert signal["peer_ip"] == peer
    assert signal["attributes"]["request_id"] == request["request_id"]
    # The correlation is what attributes a blind out-of-band callback, so it has to
    # inherit the same classification or a self-test replay would score as an exploit.
    assert correlation["synthetic"] is expected
    assert correlation["peer_ip"] == peer
    assert correlation["request_id"] == request["request_id"]


def test_a_signal_from_a_sync_handler_in_the_framework_threadpool_inherits(telemetry, collector):
    """Starlette/FastAPI run ``def`` handlers in anyio's worker thread, which copies
    the context. A target doing blocking database work is the common case, not an
    exotic one."""

    def handler(request):  # sync on purpose
        level_one()
        return JSONResponse({"ok": True})

    drive(build_asgi(telemetry, [Route("/sink", handler)]), SYNTHETIC_PEER)
    telemetry.flush()
    signal = records_of(collector.wait_for(2), "signal")[0]
    assert signal["synthetic"] is True
    assert signal["peer_ip"] == SYNTHETIC_PEER


def test_a_signal_from_asyncio_to_thread_inherits(telemetry, collector):
    async def handler(request):
        await asyncio.to_thread(level_one)
        return JSONResponse({"ok": True})

    drive(build_asgi(telemetry, [Route("/sink", handler)]), SYNTHETIC_PEER)
    telemetry.flush()
    signal = records_of(collector.wait_for(2), "signal")[0]
    assert signal["synthetic"] is True
    assert signal["peer_ip"] == SYNTHETIC_PEER


def test_a_bare_executor_loses_the_context_unless_bound(telemetry, collector):
    """The one boundary the language does not carry a context across.

    Pinned rather than hidden: ``run_in_executor``/``ThreadPoolExecutor.submit`` start
    from an empty context, so ``telemetry.bind`` exists to carry it, and this test is
    what would notice if either half of that stopped being true.
    """
    pool = concurrent.futures.ThreadPoolExecutor(2)

    async def unbound(request):
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(pool, level_one)
        return JSONResponse({"ok": True})

    async def bound(request):
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(pool, get_telemetry().bind(level_one))
        return JSONResponse({"ok": True})

    app = build_asgi(telemetry, [Route("/sink", unbound), Route("/bound", bound)])
    try:
        drive(app, SYNTHETIC_PEER)
        telemetry.flush()
        stray = records_of(collector.wait_for(2), "signal")[0]
        assert stray["synthetic"] is False and stray["peer_ip"] == ""

        collector.events.clear()
        collector.correlations.clear()
        drive(app, SYNTHETIC_PEER, "/bound")
        telemetry.flush()
        carried = records_of(collector.wait_for(2), "signal")[0]
        assert carried["synthetic"] is True
        assert carried["peer_ip"] == SYNTHETIC_PEER
        assert collector.wait_for_correlations()[0]["synthetic"] is True
    finally:
        pool.shutdown(wait=True)


def test_two_requests_in_flight_together_do_not_bleed(telemetry, collector):
    """One request stays suspended for the whole lifetime of the other."""
    released = asyncio.Event()

    async def slow(request):
        get_telemetry().signal(SIGNAL, {"payload": "before"})
        await released.wait()
        get_telemetry().signal(SIGNAL, {"payload": "after"})
        return JSONResponse({"ok": True})

    async def fast(request):
        get_telemetry().signal(SIGNAL, {"payload": "fast"})
        released.set()
        return JSONResponse({"ok": True})

    app = build_asgi(telemetry, [Route("/slow", slow), Route("/fast", fast)])

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=(SYNTHETIC_PEER, 1)), base_url="http://t"
        ) as platform_client, httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=(ORGANIC_PEER, 2)), base_url="http://t"
        ) as tool_client:
            await asyncio.gather(platform_client.get("/slow"), tool_client.get("/fast"))

    asyncio.run(exercise())
    telemetry.flush()
    signals = {s["attributes"]["payload"]: s for s in records_of(collector.wait_for(5), "signal")}
    assert set(signals) == {"before", "after", "fast"}
    # The suspended request keeps its own peer and marking across the other's whole life.
    assert signals["before"]["peer_ip"] == signals["after"]["peer_ip"] == SYNTHETIC_PEER
    assert signals["before"]["synthetic"] is signals["after"]["synthetic"] is True
    assert signals["fast"]["peer_ip"] == ORGANIC_PEER
    assert signals["fast"]["synthetic"] is False
    # Same request, same id on both sides of the suspension; different from the other's.
    assert signals["before"]["attributes"]["request_id"] == signals["after"]["attributes"]["request_id"]
    assert signals["fast"]["attributes"]["request_id"] != signals["after"]["attributes"]["request_id"]


def test_wsgi_context_is_per_thread_and_does_not_survive_the_request(telemetry, collector):
    """A threaded WSGI server reuses a small pool of threads for many requests.

    Each request must get its own context, and the thread must be clean afterwards:
    a leaked one would give the *next* request the previous caller's classification.
    """
    app = Flask(__name__)

    @app.get("/sink/<marker>")
    def sink(marker):
        level_three()
        return jsonify(ok=True)

    app.wsgi_app = TelemetryWSGIMiddleware(app.wsgi_app, telemetry=telemetry)

    def call(index: int) -> None:
        peer = SYNTHETIC_PEER if index % 2 else ORGANIC_PEER
        builder = EnvironBuilder(path=f"/sink/{index}", environ_base={"REMOTE_ADDR": peer})
        environ = builder.get_environ()
        body = app.wsgi_app(environ, lambda status, headers, exc_info=None: None)
        list(body)
        if hasattr(body, "close"):
            body.close()

    # Two workers for twenty requests: every thread serves ten in a row.
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        list(pool.map(call, range(20)))
    telemetry.flush()
    events = collector.wait_for(40)

    requests = {e["request_id"]: e for e in records_of(events, "http_request")}
    signals = records_of(events, "signal")
    assert len(requests) == 20 and len(signals) == 20
    for signal in signals:
        parent = requests[signal["attributes"]["request_id"]]
        # Marking and peer follow the request the signal belongs to, not the thread.
        assert signal["synthetic"] is parent["synthetic"]
        assert signal["peer_ip"] == parent["peer_ip"]
        assert (parent["peer_ip"] == SYNTHETIC_PEER) is parent["synthetic"]

    # Nothing left behind on the worker threads once the requests are over.
    def leftover() -> tuple:
        client = get_telemetry()
        return (client.current_request_id(), client._peer_ip())

    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        assert set(pool.map(lambda _: leftover(), range(4))) == {(None, "")}
    assert threading.current_thread() is threading.main_thread()
