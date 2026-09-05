"""WSGI middleware: Flask rule templates, enumeration, body re-injection."""

from __future__ import annotations

import json

import pytest
from flask import Blueprint, Flask, jsonify, request
from werkzeug.middleware.dispatcher import DispatcherMiddleware
from werkzeug.test import Client

from conftest import ORGANIC_PEER, SYNTHETIC_PEER
from telemetry_agent import TelemetryWSGIMiddleware, get_telemetry


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
        get_telemetry().signal(
            "shop.catalog.query.plan_anomaly", {"payload": "' UNION SELECT"}
        )
        return jsonify(ok=True)

    admin = Blueprint("admin", __name__, url_prefix="/api/admin")

    @admin.post("/imports")
    def imports():
        get_telemetry().set_auth_subject("admin:1")
        source_url = (request.get_json(silent=True) or {}).get("source_url")
        if source_url:
            get_telemetry().outbound(
                source_url, signal="shop.imports.fetch.external", param="source_url"
            )
        return jsonify(ok=True)

    app.register_blueprint(admin)
    return app


@pytest.fixture()
def client(telemetry):
    app = build_app()
    # The idiomatic Flask spelling: the url_map is found through the bound method.
    app.wsgi_app = TelemetryWSGIMiddleware(app.wsgi_app, telemetry=telemetry)
    return app.test_client()


def one_request(telemetry, collector):
    telemetry.flush()
    events = [e for e in collector.events if e["type"] == "http_request"]
    assert events, "no http_request event was emitted"
    return events[-1]


def params_of(event, location=None):
    return {p["name"]: p["sample"] for p in event["params"] if location is None or p["in"] == location}


def test_rule_template_is_reported_as_registered(client, telemetry, collector):
    client.get("/api/orders/42?q=laptop")
    event = one_request(telemetry, collector)
    assert event["route"] == "/api/orders/<int:id>"
    assert event["path"] == "/api/orders/42"
    assert event["status"] == 200
    assert params_of(event, "path") == {"id": "42"}
    assert params_of(event, "query") == {"q": "laptop"}


def test_blueprint_prefix_is_part_of_the_template(client, telemetry, collector):
    client.post("/api/admin/imports", json={"source_url": "http://x.oob.telemetry.local/a"})
    event = one_request(telemetry, collector)
    assert event["route"] == "/api/admin/imports"
    assert params_of(event, "json") == {"source_url": "http://x.oob.telemetry.local/a"}
    assert event["auth_subject"] == "admin:1"


def test_unmatched_route_keeps_the_real_path(client, telemetry, collector):
    client.get("/nope/deep")
    event = one_request(telemetry, collector)
    assert event["route"] == "<unmatched>"
    assert event["path"] == "/nope/deep"
    assert event["status"] == 404


def test_method_mismatch_still_credits_the_endpoint(client, telemetry, collector):
    client.delete("/api/products")
    event = one_request(telemetry, collector)
    assert event["route"] == "/api/products"
    assert event["status"] == 405


def test_mounted_sub_app_reports_the_public_template(telemetry, collector):
    sub = build_app()
    sub.wsgi_app = TelemetryWSGIMiddleware(sub.wsgi_app, telemetry=telemetry)
    dispatcher = DispatcherMiddleware(Flask(__name__), {"/sub": sub})
    Client(dispatcher).get("/sub/api/orders/7")
    event = one_request(telemetry, collector)
    assert event["route"] == "/sub/api/orders/<int:id>"
    assert event["path"] == "/sub/api/orders/7"


def test_body_is_still_delivered_to_the_application(client, telemetry, collector):
    payload = json.dumps({"note": "x" * 3000})
    response = client.post("/api/echo", data=payload, content_type="application/json")
    assert response.get_json()["raw"] == payload  # byte-for-byte what the client sent
    event = one_request(telemetry, collector)
    assert params_of(event, "json")["note"] == "x" * 256
    assert [p for p in event["params"] if p["name"] == "note"][0]["value_len"] == 3000


def test_form_body_is_parsed_by_the_app_and_enumerated(client, telemetry, collector):
    response = client.post("/api/echo", data={"user": "admin", "pw": "' OR 1"})
    assert response.get_json()["form"] == {"user": "admin", "pw": "' OR 1"}
    event = one_request(telemetry, collector)
    assert params_of(event, "body") == {"user": "admin", "pw": "' OR 1"}


def test_multipart_upload_reaches_the_app_and_is_enumerated(client, telemetry, collector):
    import io

    response = client.post(
        "/api/echo",
        data={"note": "hello", "doc": (io.BytesIO(b"PAYLOAD"), "../../etc/passwd")},
        content_type="multipart/form-data",
    )
    assert response.get_json()["files"] == ["doc"]
    event = one_request(telemetry, collector)
    multipart = params_of(event, "multipart")
    assert multipart["note"] == "hello"
    assert multipart["doc"] == "PAYLOAD"
    assert multipart["doc.filename"] == "../../etc/passwd"


def test_large_body_is_chained_back_to_the_application(telemetry, collector):
    telemetry.config = telemetry.config.with_overrides(max_body_bytes=32)
    app = build_app()
    app.wsgi_app = TelemetryWSGIMiddleware(app.wsgi_app, telemetry=telemetry)
    payload = "z" * 4096
    response = app.test_client().post("/api/echo", data=payload, content_type="text/plain")
    assert response.get_json()["raw"] == payload
    event = one_request(telemetry, collector)
    assert [p for p in event["params"] if p["in"] == "raw"][0]["value_len"] == 32


def test_headers_and_cookies_are_enumerated(client, telemetry, collector):
    client.set_cookie("session", "abc", domain="localhost")
    client.get(
        "/api/products",
        headers={"X-Forwarded-Host": "evil", "Referer": "http://evil/", "Accept-Encoding": "gzip"},
    )
    event = one_request(telemetry, collector)
    headers = params_of(event, "header")
    assert headers["x-forwarded-host"] == "evil"
    assert headers["referer"] == "http://evil/"
    assert "accept-encoding" not in headers
    assert params_of(event, "cookie") == {"session": "abc"}


def test_traffic_from_a_configured_peer_is_marked_synthetic(client, telemetry, collector):
    client.get("/api/sink", environ_overrides={"REMOTE_ADDR": SYNTHETIC_PEER})
    telemetry.flush()
    events = collector.wait_for(2)
    assert events and all(e["synthetic"] is True for e in events)

    collector.events.clear()
    # A header claiming to be the platform changes nothing: only the peer address does.
    client.get(
        "/api/sink",
        headers={"X-Selftest": "1", "X-Forwarded-For": SYNTHETIC_PEER},
        environ_overrides={"REMOTE_ADDR": ORGANIC_PEER},
    )
    telemetry.flush()
    events = collector.wait_for(2)
    request_event = next(e for e in events if e["type"] == "http_request")
    signal = next(e for e in events if e["type"] == "signal")
    assert request_event["synthetic"] is False
    assert signal["synthetic"] is False
    assert signal["attributes"]["request_id"] == request_event["request_id"]


def test_outbound_registration_defaults_to_the_current_route(client, telemetry, collector):
    client.post("/api/admin/imports", json={"source_url": "http://c0ffee.oob.attacker.example/x"})
    telemetry.flush()
    correlation = collector.wait_for_correlations()[-1]
    assert correlation["destination_host"] == "c0ffee.oob.attacker.example"
    assert correlation["route"] == "/api/admin/imports"
    assert correlation["param"] == "source_url"


def test_response_is_untouched_and_carries_no_marker(client, telemetry, collector):
    response = client.get("/api/products")
    assert response.get_json() == {"ok": True}
    leaked = [
        name
        for name, _ in response.headers
        if any(word in name.lower() for word in ("telemetry", "trace", "otel", "request-id"))
    ]
    assert leaked == []
    assert b"telemetry" not in response.data.lower()


def test_request_context_does_not_leak_between_requests(client, telemetry, collector):
    client.get("/api/sink")
    telemetry.flush()
    first = next(e for e in collector.events if e["type"] == "signal")["attributes"]["request_id"]
    collector.events.clear()
    client.get("/api/sink")
    telemetry.flush()
    second = next(e for e in collector.events if e["type"] == "signal")["attributes"]["request_id"]
    assert first != second
    # Outside any request the helpers must still work, just without correlation.
    telemetry.signal("shop.catalog.query.plan_anomaly")
    telemetry.flush()
    assert "attributes" not in collector.of_type("signal")[-1]


def test_a_forwarded_header_cannot_buy_a_synthetic_marking(client, telemetry, collector):
    """Regression: only the socket peer decides, on the WSGI side too."""
    for headers in (
        {"X-Forwarded-For": SYNTHETIC_PEER},
        {"X-Real-IP": SYNTHETIC_PEER},
        {"Forwarded": f"for={SYNTHETIC_PEER};proto=http"},
    ):
        client.get("/api/products", headers=headers, environ_overrides={"REMOTE_ADDR": ORGANIC_PEER})
    telemetry.flush()
    events = collector.wait_for(3)
    assert len(events) == 3
    assert all(event["synthetic"] is False for event in events)


def test_proxyfix_cannot_launder_a_claimed_address(telemetry, collector):
    """ProxyFix overwrites REMOTE_ADDR in place with a header value.

    The original peer is kept under ``werkzeug.proxy_fix.orig`` and is what classifies;
    the header value never does, however many proxies are declared.
    """
    from werkzeug.middleware.proxy_fix import ProxyFix

    app = build_app()
    app.wsgi_app = TelemetryWSGIMiddleware(app.wsgi_app, telemetry=telemetry)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1)
    app.test_client().get(
        "/api/products",
        headers={"X-Forwarded-For": SYNTHETIC_PEER},
        environ_overrides={"REMOTE_ADDR": ORGANIC_PEER},
    )
    telemetry.flush()
    event = one_request(telemetry, collector)
    assert event["synthetic"] is False
    assert event["client_ip"] == SYNTHETIC_PEER  # descriptive only, never a decision
