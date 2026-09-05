"""ASGI middleware: route templates, enumeration, body re-injection, invisibility."""

from __future__ import annotations

import json

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.testclient import TestClient

from ptaas_bench_sdk import BenchASGIMiddleware


async def ok(request):
    return JSONResponse({"ok": True})


async def echo(request):
    body = await request.body()
    return PlainTextResponse(body.decode("utf-8", "replace"))


async def sink(request):
    from ptaas_bench_sdk import get_bench

    get_bench().trigger("BENCH-SHOP-0001", oracle_kind="sink", payload="x' UNION SELECT")
    return JSONResponse({"ok": True})


async def graphql_view(request):
    from ptaas_bench_sdk import get_bench

    document = await request.json()
    get_bench().graphql(
        document.get("query"),
        variables=document.get("variables"),
        operation_name=document.get("operationName"),
        route="/graphql",
    )
    return JSONResponse({"data": None})


async def ws_view(websocket):
    from ptaas_bench_sdk import get_bench

    await websocket.accept()
    payload = await websocket.receive_text()
    get_bench().websocket(payload, route="/ws")
    await websocket.send_text("ack")
    await websocket.close()


SUB = Starlette(routes=[Route("/items/{item_id:int}", ok)])
ROUTES = [
    Route("/api/products", ok, methods=["GET"]),
    Route("/api/orders/{id}", ok, methods=["GET", "POST"]),
    Route("/api/echo", echo, methods=["POST"]),
    Route("/api/sink", sink, methods=["GET"]),
    Route("/graphql", graphql_view, methods=["POST"]),
    WebSocketRoute("/ws", ws_view),
    Mount("/sub", app=SUB),
]


@pytest.fixture()
def client(bench):
    app = BenchASGIMiddleware(Starlette(routes=ROUTES), bench=bench)
    with TestClient(app) as test_client:
        yield test_client


def one_request(bench, collector, count=1):
    bench.flush()
    events = [e for e in collector.wait_for(count) if e["type"] == "http_request"]
    assert events, "no http_request event was emitted"
    return events[-1]


def params_of(event, location=None):
    return {
        p["name"]: p["sample"]
        for p in event["params"]
        if location is None or p["in"] == location
    }


def test_route_template_not_concrete_url(client, bench, collector):
    client.get("/api/orders/42")
    event = one_request(bench, collector)
    assert event["route"] == "/api/orders/{id}"
    assert event["path"] == "/api/orders/42"
    assert event["method"] == "GET"
    assert event["status"] == 200
    assert params_of(event, "path") == {"id": "42"}


def test_mounted_sub_app_reports_the_full_template(client, bench, collector):
    client.get("/sub/items/7")
    event = one_request(bench, collector)
    assert event["route"] == "/sub/items/{item_id}"
    assert event["path"] == "/sub/items/7"
    assert params_of(event, "path") == {"item_id": "7"}


def test_unmatched_route_keeps_the_real_path(client, bench, collector):
    client.get("/nope/deep/path?x=1")
    event = one_request(bench, collector)
    assert event["route"] == "<unmatched>"
    assert event["path"] == "/nope/deep/path"
    assert event["status"] == 404
    assert params_of(event, "query") == {"x": "1"}


def test_method_mismatch_still_credits_the_endpoint(client, bench, collector):
    client.post("/api/products")
    event = one_request(bench, collector)
    assert event["route"] == "/api/products"
    assert event["status"] == 405


def test_enumerates_every_input_location(client, bench, collector):
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
    event = one_request(bench, collector)
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


def test_form_and_multipart_bodies(client, bench, collector):
    client.post("/api/orders/1", data={"user": "admin", "pw": "' OR 1"})
    event = one_request(bench, collector)
    assert params_of(event, "body") == {"user": "admin", "pw": "' OR 1"}

    collector.events.clear()
    client.post("/api/orders/1", files={"doc": ("../../etc/passwd", b"PAYLOAD")}, data={"note": "hello"})
    event = one_request(bench, collector)
    multipart = params_of(event, "multipart")
    assert multipart["note"] == "hello"
    assert multipart["doc"] == "PAYLOAD"
    assert multipart["doc.filename"] == "../../etc/passwd"


def test_body_is_still_delivered_to_the_application(client, bench, collector):
    payload = json.dumps({"deep": {"value": "x" * 3000}})
    response = client.post("/api/echo", content=payload, headers={"content-type": "application/json"})
    assert response.text == payload  # the app read exactly what the client sent
    event = one_request(bench, collector)
    assert params_of(event, "json")["deep.value"] == "x" * 256


def test_large_body_streams_through_untouched(bench, collector):
    bench.config = bench.config.with_overrides(max_body_bytes=64)
    app = BenchASGIMiddleware(Starlette(routes=ROUTES), bench=bench)
    with TestClient(app) as test_client:
        payload = "z" * 5000
        response = test_client.post("/api/echo", content=payload, headers={"content-type": "text/plain"})
    assert response.text == payload
    event = one_request(bench, collector)
    raw = [p for p in event["params"] if p["in"] == "raw"][0]
    assert raw["value_len"] == 64  # only the buffered prefix is reported


def test_selftest_header_and_seeder_agent_mark_events_synthetic(client, bench, collector):
    client.get("/api/sink", headers={"X-Bench-Selftest": "1"})
    bench.flush()
    events = collector.wait_for(2)
    assert all(e["synthetic"] is True for e in events)
    # The trigger fired by the planted sink inherits the flag, otherwise the platform
    # would credit itself with its own seeding traffic.
    assert any(e["type"] == "trigger" and e["synthetic"] for e in events)

    collector.events.clear()
    client.get("/api/products", headers={"user-agent": "ptaas-bench-seeder/1.0"})
    assert one_request(bench, collector)["synthetic"] is True

    collector.events.clear()
    client.get("/api/products")
    assert one_request(bench, collector)["synthetic"] is False


def test_trigger_is_correlated_with_its_request(client, bench, collector):
    client.get("/api/sink")
    bench.flush()
    events = collector.wait_for(2)
    request = next(e for e in events if e["type"] == "http_request")
    trigger = next(e for e in events if e["type"] == "trigger")
    assert trigger["evidence"]["request_id"] == request["request_id"]


def test_graphql_helper_merges_into_the_single_request_event(client, bench, collector):
    client.post(
        "/graphql",
        json={"query": "query Me($id:ID!){user(id:$id){email}}", "variables": {"id": "7"}, "operationName": "Me"},
    )
    bench.flush()
    requests = collector.of_type("http_request")
    assert len(requests) == 1  # one request stays one event
    graphql = params_of(requests[0], "graphql")
    assert graphql["operationName"] == "Me"
    assert graphql["variables.id"] == "7"
    assert graphql["query"].startswith("query Me(")


def test_websocket_route_and_helper(client, bench, collector):
    with client.websocket_connect("/ws?token=abc") as socket:
        socket.send_text(json.dumps({"op": "subscribe", "channel": "orders"}))
        assert socket.receive_text() == "ack"
    bench.flush()
    event = collector.of_type("http_request")[-1]
    assert event["route"] == "/ws"
    assert event["method"] == "WEBSOCKET"
    assert params_of(event, "query") == {"token": "abc"}
    assert params_of(event, "websocket") == {"message.op": "subscribe", "message.channel": "orders"}


def test_auth_subject_can_be_declared_by_the_app(bench, collector):
    async def whoami(request):
        from ptaas_bench_sdk import get_bench

        get_bench().set_auth_subject("customer:1001")
        return JSONResponse({"ok": True})

    app = BenchASGIMiddleware(Starlette(routes=[Route("/me", whoami)]), bench=bench)
    with TestClient(app) as test_client:
        test_client.get("/me")
    assert one_request(bench, collector)["auth_subject"] == "customer:1001"


def test_response_is_untouched_and_carries_no_marker(client, bench, collector):
    response = client.get("/api/products")
    assert response.json() == {"ok": True}
    leaked = [name for name in response.headers if "bench" in name.lower()]
    assert leaked == []
    assert "bench" not in response.text.lower()


def test_add_middleware_wiring_still_finds_the_router(bench, collector):
    # add_middleware hands the middleware the *inner* ASGI app, not the application,
    # so the router has to be discovered through the wrapper chain.
    app = Starlette(routes=ROUTES)
    app.add_middleware(BenchASGIMiddleware, bench=bench)
    with TestClient(app) as test_client:
        test_client.get("/api/orders/9")
    assert one_request(bench, collector)["route"] == "/api/orders/{id}"


def test_fastapi_route_templates(bench, collector):
    fastapi = pytest.importorskip("fastapi")
    api = fastapi.FastAPI()

    @api.get("/api/orders/{id}")
    def read(id: int):  # noqa: A002 - mirrors a target's handler signature
        return {"id": id}

    api.add_middleware(BenchASGIMiddleware, framework_app=api, bench=bench)
    with TestClient(api) as test_client:
        assert test_client.get("/api/orders/42").json() == {"id": 42}
    event = one_request(bench, collector)
    assert event["route"] == "/api/orders/{id}"
    assert params_of(event, "path") == {"id": "42"}
