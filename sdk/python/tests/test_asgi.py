"""ASGI middleware: route templates, enumeration, body re-injection, invisibility."""

from __future__ import annotations

import json

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.testclient import TestClient

from conftest import ORGANIC_PEER, SYNTHETIC_PEER
from telemetry_agent import TelemetryASGIMiddleware


async def ok(request):
    return JSONResponse({"ok": True})


async def echo(request):
    body = await request.body()
    return PlainTextResponse(body.decode("utf-8", "replace"))


async def sink(request):
    from telemetry_agent import get_telemetry

    get_telemetry().signal(
        "shop.catalog.query.plan_anomaly", {"payload": "x' UNION SELECT"}
    )
    return JSONResponse({"ok": True})


async def importer(request):
    from telemetry_agent import get_telemetry

    document = await request.json()
    get_telemetry().outbound(
        document["source_url"], signal="shop.imports.fetch.external", param="source_url"
    )
    return JSONResponse({"status": "accepted"})


async def graphql_view(request):
    from telemetry_agent import get_telemetry

    document = await request.json()
    get_telemetry().graphql(
        document.get("query"),
        variables=document.get("variables"),
        operation_name=document.get("operationName"),
        route="/graphql",
    )
    return JSONResponse({"data": None})


async def ws_view(websocket):
    from telemetry_agent import get_telemetry

    await websocket.accept()
    payload = await websocket.receive_text()
    get_telemetry().websocket(payload, route="/ws")
    await websocket.send_text("ack")
    await websocket.close()


SUB = Starlette(routes=[Route("/items/{item_id:int}", ok)])
ROUTES = [
    Route("/api/products", ok, methods=["GET"]),
    Route("/api/orders/{id}", ok, methods=["GET", "POST"]),
    Route("/api/echo", echo, methods=["POST"]),
    Route("/api/sink", sink, methods=["GET"]),
    Route("/api/admin/imports", importer, methods=["POST"]),
    Route("/graphql", graphql_view, methods=["POST"]),
    WebSocketRoute("/ws", ws_view),
    Mount("/sub", app=SUB),
]


@pytest.fixture()
def client(telemetry):
    app = TelemetryASGIMiddleware(Starlette(routes=ROUTES), telemetry=telemetry)
    with TestClient(app) as test_client:
        yield test_client


def one_request(telemetry, collector, count=1):
    telemetry.flush()
    events = [e for e in collector.wait_for(count) if e["type"] == "http_request"]
    assert events, "no http_request event was emitted"
    return events[-1]


def params_of(event, location=None):
    return {
        p["name"]: p["sample"]
        for p in event["params"]
        if location is None or p["in"] == location
    }


def test_route_template_not_concrete_url(client, telemetry, collector):
    client.get("/api/orders/42")
    event = one_request(telemetry, collector)
    assert event["route"] == "/api/orders/{id}"
    assert event["path"] == "/api/orders/42"
    assert event["method"] == "GET"
    assert event["status"] == 200
    assert params_of(event, "path") == {"id": "42"}


def test_mounted_sub_app_reports_the_full_template(client, telemetry, collector):
    client.get("/sub/items/7")
    event = one_request(telemetry, collector)
    assert event["route"] == "/sub/items/{item_id}"
    assert event["path"] == "/sub/items/7"
    assert params_of(event, "path") == {"item_id": "7"}


def test_unmatched_route_keeps_the_real_path(client, telemetry, collector):
    client.get("/nope/deep/path?x=1")
    event = one_request(telemetry, collector)
    assert event["route"] == "<unmatched>"
    assert event["path"] == "/nope/deep/path"
    assert event["status"] == 404
    assert params_of(event, "query") == {"x": "1"}


def test_method_mismatch_still_credits_the_endpoint(client, telemetry, collector):
    client.post("/api/products")
    event = one_request(telemetry, collector)
    assert event["route"] == "/api/products"
    assert event["status"] == 405


def test_enumerates_every_input_location(client, telemetry, collector):
    client.post(
        "/api/orders/1001?q=laptop&q=laptop&debug=",
        json={"note": "hi", "filter": {"tags": ["a", "b"]}},
        headers={
            "x-forwarded-for": "127.0.0.1",
            "referer": "http://evil/",
            "origin": "http://evil",
            "x-tenant": "acme",
            "accept-encoding": "gzip",
        },
        cookies={"session": "abc"},
    )
    event = one_request(telemetry, collector)
    assert params_of(event, "query") == {"q": "laptop", "debug": ""}
    assert params_of(event, "path") == {"id": "1001"}
    assert params_of(event, "json") == {"note": "hi", "filter.tags.0": "a", "filter.tags.1": "b"}
    assert params_of(event, "cookie") == {"session": "abc"}
    headers = params_of(event, "header")
    assert headers["x-forwarded-for"] == "127.0.0.1"
    assert headers["x-tenant"] == "acme"
    assert headers["referer"] == "http://evil/"
    assert headers["origin"] == "http://evil"
    assert "host" in headers and "user-agent" in headers
    assert "accept-encoding" not in headers  # not a plausible injection point


def test_form_and_multipart_bodies(client, telemetry, collector):
    client.post("/api/orders/1", data={"user": "admin", "pw": "' OR 1"})
    event = one_request(telemetry, collector)
    assert params_of(event, "body") == {"user": "admin", "pw": "' OR 1"}

    collector.events.clear()
    client.post("/api/orders/1", files={"doc": ("../../etc/passwd", b"PAYLOAD")}, data={"note": "hello"})
    event = one_request(telemetry, collector)
    multipart = params_of(event, "multipart")
    assert multipart["note"] == "hello"
    assert multipart["doc"] == "PAYLOAD"
    assert multipart["doc.filename"] == "../../etc/passwd"


def test_body_is_still_delivered_to_the_application(client, telemetry, collector):
    payload = json.dumps({"deep": {"value": "x" * 3000}})
    response = client.post("/api/echo", content=payload, headers={"content-type": "application/json"})
    assert response.text == payload  # the app read exactly what the client sent
    event = one_request(telemetry, collector)
    assert params_of(event, "json")["deep.value"] == "x" * 256


def test_large_body_streams_through_untouched(telemetry, collector):
    telemetry.config = telemetry.config.with_overrides(max_body_bytes=64)
    app = TelemetryASGIMiddleware(Starlette(routes=ROUTES), telemetry=telemetry)
    with TestClient(app) as test_client:
        payload = "z" * 5000
        response = test_client.post("/api/echo", content=payload, headers={"content-type": "text/plain"})
    assert response.text == payload
    event = one_request(telemetry, collector)
    raw = [p for p in event["params"] if p["in"] == "raw"][0]
    assert raw["value_len"] == 64  # only the buffered prefix is reported


def test_traffic_from_a_configured_peer_is_marked_synthetic(telemetry, collector):
    """Identification is by source address only.

    A marker header would leak through any reflection, verbose error or
    header-injection flaw and hand the tool the shape of the grader; a peer address
    cannot be reflected out of the target, and the collector re-checks it anyway.
    """
    app = TelemetryASGIMiddleware(Starlette(routes=ROUTES), telemetry=telemetry)
    with TestClient(app, client=(SYNTHETIC_PEER, 51000)) as platform_client:
        platform_client.get("/api/sink")
    telemetry.flush()
    events = collector.wait_for(2)
    assert events and all(e["synthetic"] is True for e in events)
    # The signal raised during that request inherits the marker, otherwise the platform
    # would credit itself with its own seeding traffic.
    assert any(e["type"] == "signal" and e["synthetic"] for e in events)
    # And every record reports the address the socket gave, so the collector can reach
    # the same verdict on its own.
    assert all(e["peer_ip"] == SYNTHETIC_PEER for e in events)

    collector.events.clear()
    with TestClient(app, client=(ORGANIC_PEER, 51000)) as tool_client:
        tool_client.get("/api/sink", headers={"x-selftest": "1", "user-agent": "seeder/1.0"})
    telemetry.flush()
    events = collector.wait_for(2)
    # No header and no user-agent can flip it: only the peer address decides.
    assert events and all(e["synthetic"] is False for e in events)
    assert all(e["peer_ip"] == ORGANIC_PEER for e in events)


def test_signal_is_correlated_with_its_request(client, telemetry, collector):
    client.get("/api/sink")
    telemetry.flush()
    events = collector.wait_for(2)
    request = next(e for e in events if e["type"] == "http_request")
    signal = next(e for e in events if e["type"] == "signal")
    assert signal["attributes"]["request_id"] == request["request_id"]


def test_outbound_registration_carries_the_route_of_its_request(client, telemetry, collector):
    client.post("/api/admin/imports", json={"source_url": "http://7f3a.oob.attacker.example/x"})
    telemetry.flush()
    correlation = collector.wait_for_correlations()[-1]
    assert correlation["destination_host"] == "7f3a.oob.attacker.example"
    assert correlation["signal"] == "shop.imports.fetch.external"
    assert correlation["param"] == "source_url"
    # Route and request id come from the in-flight request, so a sink only has to name
    # the destination it is about to fetch.
    assert correlation["route"] == "/api/admin/imports"
    request = next(e for e in collector.events if e["type"] == "http_request")
    assert correlation["request_id"] == request["request_id"]
    # The sinkhole sees only a hostname; the peer travels with the hint so the callback
    # can be attributed to the traffic that caused it, and classified the same way.
    assert correlation["peer_ip"] == request["peer_ip"]
    assert correlation["client_ip"] == request["client_ip"]
    assert correlation["synthetic"] is False


def test_graphql_helper_merges_into_the_single_request_event(client, telemetry, collector):
    client.post(
        "/graphql",
        json={"query": "query Me($id:ID!){user(id:$id){email}}", "variables": {"id": "7"}, "operationName": "Me"},
    )
    telemetry.flush()
    requests = collector.of_type("http_request")
    assert len(requests) == 1  # one request stays one event
    graphql = params_of(requests[0], "graphql")
    assert graphql["operationName"] == "Me"
    assert graphql["variables.id"] == "7"
    assert graphql["query"].startswith("query Me(")


def test_websocket_route_and_helper(client, telemetry, collector):
    with client.websocket_connect("/ws?token=abc") as socket:
        socket.send_text(json.dumps({"op": "subscribe", "channel": "orders"}))
        assert socket.receive_text() == "ack"
    telemetry.flush()
    event = collector.of_type("http_request")[-1]
    assert event["route"] == "/ws"
    assert event["method"] == "WEBSOCKET"
    assert params_of(event, "query") == {"token": "abc"}
    assert params_of(event, "websocket") == {"message.op": "subscribe", "message.channel": "orders"}


def test_auth_subject_can_be_declared_by_the_app(telemetry, collector):
    async def whoami(request):
        from telemetry_agent import get_telemetry

        get_telemetry().set_auth_subject("customer:1001")
        return JSONResponse({"ok": True})

    app = TelemetryASGIMiddleware(Starlette(routes=[Route("/me", whoami)]), telemetry=telemetry)
    with TestClient(app) as test_client:
        test_client.get("/me")
    assert one_request(telemetry, collector)["auth_subject"] == "customer:1001"


def test_response_is_untouched_and_carries_no_marker(client, telemetry, collector):
    response = client.get("/api/products")
    assert response.json() == {"ok": True}
    leaked = [
        name
        for name in response.headers
        if any(word in name.lower() for word in ("telemetry", "trace", "otel", "request-id"))
    ]
    assert leaked == []
    assert "telemetry" not in response.text.lower()


def test_add_middleware_wiring_still_finds_the_router(telemetry, collector):
    # add_middleware hands the middleware the *inner* ASGI app, not the application,
    # so the router has to be discovered through the wrapper chain.
    app = Starlette(routes=ROUTES)
    app.add_middleware(TelemetryASGIMiddleware, telemetry=telemetry)
    with TestClient(app) as test_client:
        test_client.get("/api/orders/9")
    assert one_request(telemetry, collector)["route"] == "/api/orders/{id}"


def test_fastapi_route_templates(telemetry, collector):
    fastapi = pytest.importorskip("fastapi")
    api = fastapi.FastAPI()

    @api.get("/api/orders/{id}")
    def read(id: int):  # noqa: A002 - mirrors a target's handler signature
        return {"id": id}

    api.add_middleware(TelemetryASGIMiddleware, framework_app=api, telemetry=telemetry)
    with TestClient(api) as test_client:
        assert test_client.get("/api/orders/42").json() == {"id": 42}
    event = one_request(telemetry, collector)
    assert event["route"] == "/api/orders/{id}"
    assert params_of(event, "path") == {"id": "42"}


def test_a_forwarded_header_cannot_buy_a_synthetic_marking(telemetry, collector):
    """Regression: the platform's own traffic is identified by socket peer only.

    If a forwarded header could decide it, a tool would send
    ``X-Forwarded-For: <platform range>`` once and erase its whole run from scoring.
    """
    app = TelemetryASGIMiddleware(Starlette(routes=ROUTES), telemetry=telemetry)
    with TestClient(app, client=(ORGANIC_PEER, 51000)) as tool_client:
        for headers in (
            {"x-forwarded-for": SYNTHETIC_PEER},
            {"x-forwarded-for": f"{SYNTHETIC_PEER}, 10.99.0.8"},
            {"x-real-ip": SYNTHETIC_PEER},
            {"forwarded": f"for={SYNTHETIC_PEER};proto=http"},
            {"true-client-ip": SYNTHETIC_PEER, "client-ip": SYNTHETIC_PEER},
        ):
            tool_client.get("/api/sink", headers=headers)
    telemetry.flush()
    events = collector.wait_for(10)
    assert len(events) == 10
    assert all(event["synthetic"] is False for event in events)
    # The socket peer is reported, never the announced one, so the collector's own
    # check cannot be fooled by the same header either.
    assert all(event["peer_ip"] == ORGANIC_PEER for event in events)
    requests = [event for event in events if event["type"] == "http_request"]
    assert all(event["client_ip"] == ORGANIC_PEER for event in requests)


def test_a_rewritten_peer_that_the_caller_announced_is_not_trusted(telemetry, collector):
    """Defence in depth for a deployment that resolves proxy headers upstream.

    Some servers replace scope["client"] with the forwarded value before the
    application sees it. When the address we are handed is one the caller itself
    announced, it is the caller's claim, not the socket's, so it cannot classify.
    """
    app = TelemetryASGIMiddleware(Starlette(routes=ROUTES), telemetry=telemetry)
    with TestClient(app, client=(SYNTHETIC_PEER, 51000)) as spoofer:
        spoofer.get("/api/products", headers={"x-forwarded-for": SYNTHETIC_PEER})
    telemetry.flush()
    event = one_request(telemetry, collector)
    assert event["synthetic"] is False
    # Reported as nothing rather than as a socket address: the collector must not be
    # handed a claim dressed up as an observation.
    assert event["peer_ip"] == ""
    assert event["client_ip"] == SYNTHETIC_PEER  # description only


def test_host_is_reported_and_still_enumerated(client, telemetry, collector):
    """The scorer keys routes by host: the same path can be exposed on one virtual host
    and hardened on another, so a visit has to say which one it reached."""
    client.get("/api/products", headers={"host": "Shop.Example:8443"})
    event = one_request(telemetry, collector)
    assert event["host"] == "shop.example"  # lower-cased, port stripped
    # Still described as an input as well: Host is one, and a sink can key off it.
    assert params_of(event, "header")["host"] == "Shop.Example:8443"


def test_http2_authority_is_used_when_there_is_no_host_header(telemetry, collector):
    import asyncio

    app = TelemetryASGIMiddleware(Starlette(routes=ROUTES), telemetry=telemetry)
    scope = {
        "type": "http",
        "http_version": "2",
        "method": "GET",
        "path": "/api/products",
        "raw_path": b"/api/products",
        "query_string": b"",
        "root_path": "",
        "client": (ORGANIC_PEER, 4444),
        "headers": [(b":authority", b"Shop.Example:8443"), (b"user-agent", b"h2")],
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        return None

    asyncio.run(app(scope, receive, send))
    telemetry.flush()
    assert one_request(telemetry, collector)["host"] == "shop.example"


def test_a_request_without_a_host_reports_none(telemetry, collector):
    import asyncio

    app = TelemetryASGIMiddleware(Starlette(routes=ROUTES), telemetry=telemetry)
    scope = {
        "type": "http",
        "http_version": "1.0",  # a Host header is not mandatory in HTTP/1.0
        "method": "GET",
        "path": "/api/products",
        "raw_path": b"/api/products",
        "query_string": b"",
        "root_path": "",
        "client": (ORGANIC_PEER, 4444),
        "headers": [],
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        return None

    asyncio.run(app(scope, receive, send))
    telemetry.flush()
    # Absent rather than defaulted: the scorer reports an unresolved host as unresolved,
    # which beats a wrong one resolving silently against the inventory.
    assert "host" not in one_request(telemetry, collector)
