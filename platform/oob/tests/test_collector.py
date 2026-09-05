"""Collector integration: the event shape the OpenAPI mandates, and what happens when
the collector is slow, broken or gone."""

from __future__ import annotations

import socket
import time

from conftest import ZONE, dns_udp, http_request


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_event_matches_the_openapi_oob_shape(make_service, fake_collector):
    service = make_service(collector_url=fake_collector.url, flush_interval=0.1)
    dns_udp(service.ports["dns_udp"], f"shop0031.{ZONE}")

    (event,) = fake_collector.wait_for(1, timeout=5)
    assert event["type"] == "oob"
    assert event["app"] == "oob"  # the service reports as its own app key
    assert event["token"] == "shop0031"
    assert event["channel"] == "dns"
    assert event["source_ip"] == "127.0.0.1"
    assert isinstance(event["ts"], float) and event["ts"] > 1_600_000_000
    assert event["synthetic"] is False
    assert 0 < len(event["raw"]) <= 2048
    assert set(event) == {"type", "app", "ts", "synthetic", "token", "channel", "source_ip", "raw"}


def test_dynamic_token_is_reported_under_its_base(make_service, fake_collector):
    """Scoring correlates against the catalog's canary_token, so the nonce must not
    leak into the `token` field -- it stays in `raw`."""
    service = make_service(collector_url=fake_collector.url, flush_interval=0.1)
    dns_udp(service.ports["dns_udp"], f"shop0031-9f2c.{ZONE}")
    (event,) = fake_collector.wait_for(1, timeout=5)
    assert event["token"] == "shop0031"
    assert "shop0031-9f2c" in event["raw"]


def test_unknown_callback_is_still_posted(make_service, fake_collector):
    service = make_service(collector_url=fake_collector.url, flush_interval=0.1)
    http_request(service.ports["http"], "/", host="127.0.0.1")
    (event,) = fake_collector.wait_for(1, timeout=5)
    # The schema requires a string token, so an unattributable hit gets the sentinel and
    # everything identifying goes to raw.
    assert event["token"] == "unknown"
    assert "token=unknown" in event["raw"]


def test_selftest_header_marks_the_event_synthetic(make_service, fake_collector):
    service = make_service(collector_url=fake_collector.url, flush_interval=0.1)
    http_request(
        service.ports["http"],
        "/shop0031",
        host=ZONE,
        headers={"X-Bench-Selftest": "1"},
    )
    (event,) = fake_collector.wait_for(1, timeout=5)
    assert event["synthetic"] is True


def test_events_are_batched(make_service, fake_collector):
    service = make_service(collector_url=fake_collector.url, flush_interval=0.3)
    for index in range(5):
        dns_udp(service.ports["dns_udp"], f"aaaa000{index}.{ZONE}")
    events = fake_collector.wait_for(5, timeout=5)
    assert len(events) == 5
    assert fake_collector.requests < 5  # batched, not one POST per callback


def test_listeners_keep_working_when_the_collector_is_down(make_service):
    """The whole point of the bounded queue: a dead collector must be invisible to the
    listeners and must never cost a callback in the local store."""
    service = make_service(
        collector_url=f"http://127.0.0.1:{_free_port()}", flush_interval=0.1
    )
    for index in range(3):
        response = dns_udp(service.ports["dns_udp"], f"aaaa000{index}.{ZONE}")
        assert response  # answered normally despite the collector being unreachable
    http_request(service.ports["http"], "/shop0031", host=ZONE)

    assert len(service.store.wait_for(4, timeout=5)) == 4

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and service.collector.stats.failed < 1:
        time.sleep(0.05)
    stats = service.collector.stats
    assert stats.enqueued == 4 and stats.failed >= 1 and stats.posted == 0
    assert stats.last_error


def test_a_full_queue_drops_instead_of_blocking(make_service):
    service = make_service(collector_url="http://127.0.0.1:1", queue_size=2)
    for index in range(20):
        service.collector.submit({"type": "oob", "app": "oob", "token": "t", "channel": "dns"})
    assert service.collector.stats.dropped > 0
    # And the service is still perfectly able to answer and record.
    dns_udp(service.ports["dns_udp"], f"shop0031.{ZONE}")
    assert service.store.wait_for(1, timeout=5)[0].token == "shop0031"


def test_no_collector_configured_is_a_supported_mode(service):
    dns_udp(service.ports["dns_udp"], f"shop0031.{ZONE}")
    assert service.store.wait_for(1, timeout=5)[0].token == "shop0031"
    assert service.collector.enabled is False
    assert service.collector.stats.dropped == 1


def test_a_slow_collector_does_not_slow_a_listener(make_service):
    """A listener answers in milliseconds even when every POST is going to hang: the
    flush thread absorbs it, and a canary that stalled would become a timing oracle."""
    import http.server
    import threading

    class Slow(http.server.BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            time.sleep(3)
            self.send_response(202)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Slow)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        service = make_service(
            collector_url=f"http://127.0.0.1:{server.server_address[1]}",
            flush_interval=0.05,
            collector_timeout=0.2,
        )
        started = time.monotonic()
        for index in range(5):
            dns_udp(service.ports["dns_udp"], f"aaaa000{index}.{ZONE}")
        elapsed = time.monotonic() - started
        assert elapsed < 1.0, f"listeners were dragged down by the collector ({elapsed:.2f}s)"
        assert len(service.store.wait_for(5, timeout=5)) == 5
    finally:
        server.shutdown()
        server.server_close()
