"""The examples are the pattern target apps copy, so they are tested like code.

Each one must: report its route template, keep quiet when the endpoint is merely
visited, and fire exactly one trigger when the planted flaw is really exploited.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def load(name: str, bench):
    import ptaas_bench_sdk
    from ptaas_bench_sdk import _client

    _client._ACTIVE = bench
    # init_bench() inside the example would replace the fixture's client, so it is
    # neutralised: the example's own wiring is what is under test, not its config.
    original_init = ptaas_bench_sdk.init_bench
    ptaas_bench_sdk.init_bench = lambda *args, **kwargs: bench
    try:
        spec = importlib.util.spec_from_file_location(f"example_{name}", EXAMPLES / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        ptaas_bench_sdk.init_bench = original_init
        _client._ACTIVE = bench


def test_flask_example_sqli(bench, collector):
    module = load("flask_minimal", bench)
    client = module.app.test_client()

    assert client.get("/api/products?q=laptop").status_code == 200
    bench.flush()
    assert collector.of_type("trigger") == []  # visiting is not exploiting
    assert collector.of_type("http_request")[-1]["route"] == "/api/products"

    payload = "x' UNION SELECT id,email,password_hash FROM users--"
    assert client.get("/api/products", query_string={"q": payload}).status_code == 200
    bench.flush()
    triggers = collector.of_type("trigger")
    assert len(triggers) == 1
    assert triggers[0]["vuln_id"] == "BENCH-SHOP-0001"
    assert triggers[0]["oracle_kind"] == "sink"
    assert triggers[0]["evidence"]["payload"] == payload


def test_fastapi_example_bola(bench, collector):
    pytest.importorskip("fastapi")
    from starlette.testclient import TestClient

    module = load("fastapi_minimal", bench)
    with TestClient(module.app) as client:
        assert client.get("/api/orders/1001", headers={"cookie": "session=alice"}).status_code == 200
        bench.flush()
        assert collector.of_type("trigger") == []  # own order: no trigger
        event = collector.of_type("http_request")[-1]
        assert event["route"] == "/api/orders/{id}"
        assert event["auth_subject"] == "customer:1"

        assert client.get("/api/orders/1002").status_code == 401
        bench.flush()
        assert collector.of_type("trigger") == []  # anonymous: no trigger

        assert client.get("/api/orders/1002", headers={"cookie": "session=alice"}).status_code == 200
    bench.flush()
    triggers = collector.of_type("trigger")
    assert len(triggers) == 1
    assert triggers[0]["vuln_id"] == "BENCH-SHOP-0014"
    assert triggers[0]["oracle_kind"] == "differential"
