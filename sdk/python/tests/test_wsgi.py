"""WSGI middleware: Flask rule templates, enumeration, body re-injection."""

from __future__ import annotations

import json

import pytest
from flask import Blueprint, Flask, jsonify, request
from werkzeug.middleware.dispatcher import DispatcherMiddleware
from werkzeug.test import Client

from ptaas_bench_sdk import BenchWSGIMiddleware, get_bench


def build_app() -> Flask:
    app = Flask(__name__)

    @app.get("/api/products")
    def products():
        return jsonify(ok=True)

    @app.route("/api/orders/<int:id>", methods=["GET", "POST"])
    def order(id):  # noqa: A002 - mirrors a target's handler signature
        return jsonify(id=id)

    @app.post("/api/echo")
    def echo():
        # Reads the body twice, the way a real handler does (raw then parsed).
        raw = request.get_data()
        return jsonify(raw=raw.decode(), form=dict(request.form), files=sorted(request.files))

    @app.get("/api/sink")
    def sink():
        get_bench().trigger("BENCH-SHOP-0001", oracle_kind="sink", payload="' UNION SELECT")
        return jsonify(ok=True)

    admin = Blueprint("admin", __name__, url_prefix="/api/admin")

    @admin.post("/imports")
    def imports():
        get_bench().set_auth_subject("admin:1")
        return jsonify(ok=True)

    app.register_blueprint(admin)
    return app


@pytest.fixture()
def client(bench):
    app = build_app()
    # The idiomatic Flask spelling: the url_map is found through the bound method.
    app.wsgi_app = BenchWSGIMiddleware(app.wsgi_app, bench=bench)
    return app.test_client()


def one_request(bench, collector):
    bench.flush()
    events = [e for e in collector.events if e["type"] == "http_request"]
    assert events, "no http_request event was emitted"
    return events[-1]


def params_of(event, location=None):
    return {p["name"]: p["sample"] for p in event["params"] if location is None or p["in"] == location}


def test_rule_template_is_reported_as_registered(client, bench, collector):
    client.get("/api/orders/42?q=laptop")
    event = one_request(bench, collector)
    assert event["route"] == "/api/orders/<int:id>"
    assert event["path"] == "/api/orders/42"
    assert event["status"] == 200
    assert params_of(event, "path") == {"id": "42"}
    assert params_of(event, "query") == {"q": "laptop"}


def test_blueprint_prefix_is_part_of_the_template(client, bench, collector):
    client.post("/api/admin/imports", json={"source_url": "http://x.oob.bench.local/a"})
    event = one_request(bench, collector)
    assert event["route"] == "/api/admin/imports"
    assert params_of(event, "json") == {"source_url": "http://x.oob.bench.local/a"}
    assert event["auth_subject"] == "admin:1"


def test_unmatched_route_keeps_the_real_path(client, bench, collector):
    client.get("/nope/deep")
    event = one_request(bench, collector)
    assert event["route"] == "<unmatched>"
    assert event["path"] == "/nope/deep"
    assert event["status"] == 404


def test_method_mismatch_still_credits_the_endpoint(client, bench, collector):
    client.delete("/api/products")
    event = one_request(bench, collector)
    assert event["route"] == "/api/products"
    assert event["status"] == 405


def test_mounted_sub_app_reports_the_public_template(bench, collector):
    sub = build_app()
    sub.wsgi_app = BenchWSGIMiddleware(sub.wsgi_app, bench=bench)
    dispatcher = DispatcherMiddleware(Flask(__name__), {"/sub": sub})
    Client(dispatcher).get("/sub/api/orders/7")
    event = one_request(bench, collector)
    assert event["route"] == "/sub/api/orders/<int:id>"
    assert event["path"] == "/sub/api/orders/7"


def test_body_is_still_delivered_to_the_application(client, bench, collector):
    payload = json.dumps({"note": "x" * 3000})
    response = client.post("/api/echo", data=payload, content_type="application/json")
    assert response.get_json()["raw"] == payload  # byte-for-byte what the client sent
    event = one_request(bench, collector)
    assert params_of(event, "json")["note"] == "x" * 256
    assert [p for p in event["params"] if p["name"] == "note"][0]["value_len"] == 3000


def test_form_body_is_parsed_by_the_app_and_enumerated(client, bench, collector):
    response = client.post("/api/echo", data={"user": "admin", "pw": "' OR 1"})
    assert response.get_json()["form"] == {"user": "admin", "pw": "' OR 1"}
    event = one_request(bench, collector)
    assert params_of(event, "body") == {"user": "admin", "pw": "' OR 1"}


def test_multipart_upload_reaches_the_app_and_is_enumerated(client, bench, collector):
    import io

    response = client.post(
        "/api/echo",
        data={"note": "hello", "doc": (io.BytesIO(b"PAYLOAD"), "../../etc/passwd")},
        content_type="multipart/form-data",
    )
    assert response.get_json()["files"] == ["doc"]
    event = one_request(bench, collector)
    multipart = params_of(event, "multipart")
    assert multipart["note"] == "hello"
    assert multipart["doc"] == "PAYLOAD"
    assert multipart["doc.filename"] == "../../etc/passwd"


def test_large_body_is_chained_back_to_the_application(bench, collector):
    bench.config = bench.config.with_overrides(max_body_bytes=32)
    app = build_app()
    app.wsgi_app = BenchWSGIMiddleware(app.wsgi_app, bench=bench)
    payload = "z" * 4096
    response = app.test_client().post("/api/echo", data=payload, content_type="text/plain")
    assert response.get_json()["raw"] == payload
    event = one_request(bench, collector)
    assert [p for p in event["params"] if p["in"] == "raw"][0]["value_len"] == 32


def test_headers_and_cookies_are_enumerated(client, bench, collector):
    client.set_cookie("session", "abc", domain="localhost")
    client.get(
        "/api/products",
        headers={"X-Forwarded-Host": "evil", "Referer": "http://evil/", "Accept-Encoding": "gzip"},
    )
    event = one_request(bench, collector)
    headers = params_of(event, "header")
    assert headers["x-forwarded-host"] == "evil"
    assert headers["referer"] == "http://evil/"
    assert "accept-encoding" not in headers
    assert params_of(event, "cookie") == {"session": "abc"}


def test_synthetic_flagging_and_trigger_correlation(client, bench, collector):
    client.get("/api/sink", headers={"X-Bench-Selftest": "1"})
    bench.flush()
    assert all(e["synthetic"] is True for e in collector.events)

    collector.events.clear()
    client.get("/api/sink")
    bench.flush()
    request_event = next(e for e in collector.events if e["type"] == "http_request")
    trigger = next(e for e in collector.events if e["type"] == "trigger")
    assert request_event["synthetic"] is False
    assert trigger["synthetic"] is False
    assert trigger["evidence"]["request_id"] == request_event["request_id"]


def test_response_is_untouched_and_carries_no_marker(client, bench, collector):
    response = client.get("/api/products")
    assert response.get_json() == {"ok": True}
    assert [name for name, _ in response.headers if "bench" in name.lower()] == []
    assert b"bench" not in response.data.lower()


def test_request_context_does_not_leak_between_requests(client, bench, collector):
    client.get("/api/sink")
    bench.flush()
    first = next(e for e in collector.events if e["type"] == "trigger")["evidence"]["request_id"]
    collector.events.clear()
    client.get("/api/sink")
    bench.flush()
    second = next(e for e in collector.events if e["type"] == "trigger")["evidence"]["request_id"]
    assert first != second
    # Outside any request the helpers must still work, just without correlation.
    bench.trigger("BENCH-SHOP-0001")
    bench.flush()
    assert "request_id" not in collector.of_type("trigger")[-1].get("evidence", {})
