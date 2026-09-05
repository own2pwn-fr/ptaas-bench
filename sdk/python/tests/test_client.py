"""Queue, batching, drop accounting, record shapes and peer classification.

Vocabulary note: these tests are never shipped into a target container, so they name
the benchmark's real reasons. The library itself never does -- see test_no_tells.py.
"""

from __future__ import annotations

import threading
import time

from telemetry_agent import TelemetryClient, config_from_env, get_telemetry, init_telemetry


def test_signal_and_note_match_the_contract(telemetry, collector):
    telemetry.signal(
        "shop.catalog.query.plan_anomaly",
        {"payload": "x' UNION SELECT", "detail": "row shape outside projection"},
    )
    telemetry.note("seeded 3 orders")
    telemetry.flush()
    events = collector.wait_for(2)

    signal = next(e for e in events if e["type"] == "signal")
    # No catalog id on the wire and none in the target's source: the scorer maps this
    # opaque name back to the vulnerability through the catalog's oracle.signal field.
    assert signal["signal"] == "shop.catalog.query.plan_anomaly"
    assert "vuln_id" not in signal
    assert signal["app"] == "testapp"
    assert signal["synthetic"] is False
    assert isinstance(signal["ts"], float)
    assert signal["attributes"]["payload"] == "x' UNION SELECT"
    assert signal["attributes"]["detail"] == "row shape outside projection"

    note = next(e for e in events if e["type"] == "note")
    assert note["message"] == "seeded 3 orders"


def test_signal_attributes_are_clipped(telemetry, collector):
    telemetry.signal("shop.catalog.query.plan_anomaly", {"payload": "A" * 5000, "detail": "B" * 5000})
    telemetry.flush()
    attributes = collector.wait_for(1)[0]["attributes"]
    assert len(attributes["payload"]) == 1024
    assert len(attributes["detail"]) == 1024


def test_records_are_exported_to_the_otlp_path(telemetry, collector):
    telemetry.note("hello")
    telemetry.flush()
    collector.wait_for(1)
    assert collector.paths == ["/v1/traces"]


def test_outbound_registers_a_correlation_on_its_own_path(telemetry, collector):
    telemetry.outbound(
        "http://a1b2c3.oob.attacker.example/x?y=1",
        signal="shop.imports.fetch.external",
        param="source_url",
        route="/api/admin/imports",
        request_id="req-1",
    )
    correlations = collector.wait_for_correlations()
    assert correlations == [
        {
            "app": "testapp",
            "ts": correlations[0]["ts"],
            "synthetic": False,
            "peer_ip": "",  # registered outside a request: no socket to report
            "destination_host": "a1b2c3.oob.attacker.example",
            "signal": "shop.imports.fetch.external",
            "param": "source_url",
            "route": "/api/admin/imports",
            "request_id": "req-1",
        }
    ]
    assert collector.paths == ["/v1/correlations"]
    # Links never travel with records: different lane, different path, own connection.
    assert collector.events == []


def test_outbound_does_not_wait_for_the_export_tick(collector):
    """The callback it explains lands within microseconds of the fetch.

    A link that rode the 250 ms batch tick would reach the collector after the
    sinkhole had already seen (and failed to attribute) the lookup, which would make
    every blind out-of-band flaw look unexploitable.
    """
    config = config_from_env(
        service="testapp", endpoint=collector.url, enabled=True, flush_interval=30.0
    )
    client = TelemetryClient(config)
    try:
        started = time.monotonic()
        client.outbound("http://f00d.oob.attacker.example/x", signal="shop.imports.fetch.external")
        correlations = collector.wait_for_correlations(timeout=5.0)
        elapsed = time.monotonic() - started
        assert correlations and correlations[0]["destination_host"] == "f00d.oob.attacker.example"
        assert elapsed < 1.0, f"took {elapsed:.3f}s with a 30s export interval"
        assert client.stats()["links_sent"] == 1
    finally:
        client.close(1.0)


def test_every_record_carries_the_observed_peer(telemetry, collector):
    """The collector cannot see the original client -- the peer *it* sees is the
    target container -- so its independent synthetic check only works on what the
    middleware observed and reported here."""
    telemetry.signal("shop.catalog.query.plan_anomaly", {"payload": "x"})
    telemetry.note("hello")
    telemetry.flush()
    events = collector.wait_for(2)
    assert events and all("peer_ip" in event for event in events)
    assert all(event["peer_ip"] == "" for event in events)  # no request in flight


def test_signal_names_must_be_metric_shaped(telemetry, collector):
    for bad in ("BENCH-SHOP-0001", "shop", "Shop.Catalog", "shop..query", "shop.query!", ""):
        telemetry.signal(bad, {"payload": "x"})
    telemetry.signal("shop.catalog.query.plan_anomaly", {"payload": "x"})
    telemetry.flush()
    events = collector.wait_for(1)
    assert [e["signal"] for e in events] == ["shop.catalog.query.plan_anomaly"]
    assert telemetry.stats()["invalid_signals"] == 6


def test_an_explicit_request_id_in_attributes_wins(telemetry, collector):
    telemetry.signal("shop.orders.subject.mismatch", {"payload": "1002", "request_id": "earlier"})
    telemetry.flush()
    assert collector.wait_for(1)[0]["attributes"]["request_id"] == "earlier"


def test_outbound_accepts_a_bare_host_and_odd_urls(telemetry, collector):
    for destination, expected in (
        ("evil.example", "evil.example"),
        ("http://user:pw@evil.example:8080/path", "evil.example"),
        ("ftp://[2001:db8::1]:21/x", "2001:db8::1"),
        ("evil.example:9000/x", "evil.example"),
    ):
        telemetry.outbound(destination, signal="s")
    assert [c["destination_host"] for c in collector.wait_for_correlations(4)] == [
        "evil.example",
        "evil.example",
        "2001:db8::1",
        "evil.example",
    ]


def test_synthetic_peers_are_recognised_by_address(telemetry):
    assert telemetry.is_synthetic_peer("10.99.0.7") is True
    assert telemetry.is_synthetic_peer("203.0.113.9") is False
    # Neither a hostname nor an empty value nor rubbish may raise on the request path.
    assert telemetry.is_synthetic_peer("testclient") is False
    assert telemetry.is_synthetic_peer("") is False
    assert telemetry.is_synthetic_peer(None) is False


def test_synthetic_cidrs_come_from_the_environment(monkeypatch, collector):
    monkeypatch.setenv("TELEMETRY_SYNTHETIC_CIDRS", "10.99.0.0/16, 2001:db8::/32 ,garbage/99")
    client = TelemetryClient(config_from_env(service="t", endpoint=collector.url))
    try:
        assert client.is_synthetic_peer("10.99.4.1") is True
        assert client.is_synthetic_peer("2001:db8::5") is True
        assert client.is_synthetic_peer("10.98.0.1") is False
    finally:
        client.close(0.5)


def test_batches_never_exceed_500_events(telemetry, collector):
    for index in range(1200):
        telemetry.note(f"n{index}")
    assert telemetry.flush(timeout=10.0)
    events = collector.wait_for(1200, timeout=10.0)
    assert len(events) == 1200
    assert collector.batches, "expected at least one batch"
    assert max(len(batch) for batch in collector.batches) <= 500


def test_a_full_batch_is_exported_without_waiting_for_the_interval(collector):
    # flush_interval is deliberately huge: the only way these arrive is the
    # "queue reached batch_max" wake-up.
    config = config_from_env(
        service="testapp", endpoint=collector.url, enabled=True, flush_interval=30.0, batch_max=20
    )
    client = TelemetryClient(config)
    try:
        for index in range(20):
            client.note(index)
        assert len(collector.wait_for(20, timeout=3.0)) == 20
    finally:
        client.close(1.0)


def test_full_queue_drops_the_oldest_and_counts_it(monkeypatch):
    config = config_from_env(service="testapp", endpoint="http://127.0.0.1:1", enabled=True, queue_max=5)
    client = TelemetryClient(config)
    # Keep the exporter out of it so the queue state is deterministic.
    monkeypatch.setattr(client, "_ensure_worker", lambda: None)
    for index in range(20):
        client.emit({"type": "note", "app": "testapp", "message": index})

    stats = client.stats()
    assert stats["queued"] == 5
    assert stats["dropped"] == 15
    assert stats["enqueued"] == 20
    # Newest kept: during a scan the last records are the ones still worth having.
    assert [record["message"] for record in client._queue] == [15, 16, 17, 18, 19]


def test_a_failing_collector_only_moves_counters(collector):
    collector.status = 500
    config = config_from_env(service="testapp", endpoint=collector.url, enabled=True, flush_interval=0.02)
    client = TelemetryClient(config)
    try:
        client.note("hello")
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and client.stats()["send_failures"] == 0:
            time.sleep(0.01)
        stats = client.stats()
        assert stats["send_failures"] >= 1
        assert stats["sent"] == 0
        assert stats["queued"] == 0  # discarded, never re-queued: the queue cannot grow
    finally:
        client.close(1.0)


def test_disabled_client_records_nothing_and_starts_no_thread(collector):
    config = config_from_env(service="testapp", endpoint=collector.url, enabled=False)
    client = TelemetryClient(config)
    before = threading.active_count()
    for _ in range(100):
        client.note("x")
        client.signal("shop.catalog.query.plan_anomaly")
        client.outbound("http://evil.example/x")
    assert client.stats()["enqueued"] == 0
    assert threading.active_count() == before
    assert collector.events == [] and collector.correlations == []


def test_init_telemetry_reads_the_environment(monkeypatch, collector):
    monkeypatch.setenv("TELEMETRY_SERVICE", "shopfront")
    monkeypatch.setenv("TELEMETRY_ENDPOINT", collector.url)
    client = init_telemetry()
    try:
        assert client.config.service == "shopfront"
        assert client.config.endpoint == collector.url
        assert get_telemetry() is client
    finally:
        client.close(1.0)


def test_env_can_disable_the_agent(monkeypatch):
    monkeypatch.setenv("TELEMETRY_ENABLED", "0")
    client = init_telemetry()
    assert client.config.enabled is False


def test_module_level_shortcuts_use_the_active_client(telemetry, collector):
    from telemetry_agent import note, outbound, signal

    signal("shop.catalog.query.plan_anomaly", {"payload": "x"})
    note("hi")
    outbound("http://evil.example/a", signal="shop.imports.fetch.external")
    telemetry.flush()
    events = collector.wait_for(2)
    assert {e["type"] for e in events} == {"signal", "note"}
