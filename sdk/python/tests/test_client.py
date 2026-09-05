"""Queue, batching, drop accounting and event shapes."""

from __future__ import annotations

import threading
import time

from ptaas_bench_sdk import BenchClient, config_from_env, get_bench, init_bench


def test_trigger_and_note_match_the_contract(bench, collector):
    bench.trigger("BENCH-SHOP-0001", oracle_kind="sink", payload="x' UNION SELECT", detail="union parsed")
    bench.note("seeded 3 orders")
    bench.flush()
    events = collector.wait_for(2)

    trigger = next(e for e in events if e["type"] == "trigger")
    assert trigger["vuln_id"] == "BENCH-SHOP-0001"
    assert trigger["oracle_kind"] == "sink"
    assert trigger["app"] == "testapp"
    assert trigger["synthetic"] is False
    assert isinstance(trigger["ts"], float)
    assert trigger["evidence"]["payload"] == "x' UNION SELECT"
    assert trigger["evidence"]["detail"] == "union parsed"

    note = next(e for e in events if e["type"] == "note")
    assert note["message"] == "seeded 3 orders"


def test_evidence_is_clipped_to_the_contract_limit(bench, collector):
    bench.trigger("BENCH-SHOP-0001", payload="A" * 5000, detail="B" * 5000)
    bench.flush()
    evidence = collector.wait_for(1)[0]["evidence"]
    assert len(evidence["payload"]) == 1024
    assert len(evidence["detail"]) == 1024


def test_batches_never_exceed_500_events(bench, collector):
    for index in range(1200):
        bench.note(f"n{index}")
    assert bench.flush(timeout=10.0)
    events = collector.wait_for(1200, timeout=10.0)
    assert len(events) == 1200
    assert collector.batches, "expected at least one batch"
    assert max(len(batch) for batch in collector.batches) <= 500


def test_a_full_batch_is_flushed_without_waiting_for_the_interval(collector):
    # flush_interval is deliberately huge: the only way these arrive is the
    # "queue reached batch_max" wake-up.
    config = config_from_env(
        app="testapp", collector_url=collector.url, enabled=True, flush_interval=30.0, batch_max=20
    )
    client = BenchClient(config)
    try:
        for index in range(20):
            client.note(index)
        assert len(collector.wait_for(20, timeout=3.0)) == 20
    finally:
        client.close(1.0)


def test_full_queue_drops_the_oldest_and_counts_it(monkeypatch):
    config = config_from_env(app="testapp", collector_url="http://127.0.0.1:1", enabled=True, queue_max=5)
    client = BenchClient(config)
    # Keep the flusher out of it so the queue state is deterministic.
    monkeypatch.setattr(client, "_ensure_worker", lambda: None)
    for index in range(20):
        client.emit({"type": "note", "app": "testapp", "message": index})

    stats = client.stats()
    assert stats["queued"] == 5
    assert stats["dropped"] == 15
    assert stats["enqueued"] == 20
    # Newest kept: during a scan the last events are the ones still worth having.
    assert [e["message"] for e in client._queue] == [15, 16, 17, 18, 19]


def test_a_failing_collector_only_moves_counters(collector):
    collector.status = 500
    config = config_from_env(app="testapp", collector_url=collector.url, enabled=True, flush_interval=0.02)
    client = BenchClient(config)
    try:
        client.note("hello")
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and client.stats()["send_failures"] == 0:
            time.sleep(0.01)
        stats = client.stats()
        assert stats["send_failures"] >= 1
        assert stats["sent"] == 0
        assert stats["queued"] == 0  # dropped, never re-queued: the queue cannot grow
    finally:
        client.close(1.0)


def test_disabled_client_emits_nothing_and_starts_no_thread(collector):
    config = config_from_env(app="testapp", collector_url=collector.url, enabled=False)
    client = BenchClient(config)
    before = threading.active_count()
    for _ in range(100):
        client.note("x")
        client.trigger("BENCH-SHOP-0001")
    assert client.stats()["enqueued"] == 0
    assert threading.active_count() == before
    assert collector.events == []


def test_init_bench_reads_the_environment(monkeypatch, collector):
    monkeypatch.setenv("BENCH_APP", "shopfront")
    monkeypatch.setenv("BENCH_COLLECTOR_URL", collector.url)
    client = init_bench()
    try:
        assert client.config.app == "shopfront"
        assert client.config.collector_url == collector.url
        assert get_bench() is client
    finally:
        client.close(1.0)


def test_env_can_disable_instrumentation(monkeypatch):
    monkeypatch.setenv("BENCH_ENABLED", "0")
    client = init_bench()
    assert client.config.enabled is False
