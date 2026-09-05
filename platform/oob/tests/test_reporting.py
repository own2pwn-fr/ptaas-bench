"""What we report, and what happens when the endpoint is slow, broken or gone."""

from __future__ import annotations

import socket
import time

from conftest import ZONE, dns_udp, http_request


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_event_matches_the_protocol(make_service, endpoint):
    service = make_service(telemetry_url=endpoint.url, flush_interval=0.1)
    dns_udp(service.ports["dns_udp"], f"shop0031.{ZONE}")

    (event,) = endpoint.wait_for(1, timeout=5)
    assert event["type"] == "oob"
    assert event["channel"] == "dns"
    assert event["token"] == "shop0031"
    assert event["source_ip"] == "127.0.0.1"
    # Present under both names: `client_ip` is the field every event type carries and the
    # one the endpoint tests against its own networks.
    assert event["client_ip"] == "127.0.0.1"
    assert isinstance(event["ts"], float) and event["ts"] > 1_600_000_000
    assert event["synthetic"] is False
    assert 0 < len(event["raw"]) <= 2048
    assert set(event) == {
        "type", "app", "ts", "synthetic", "token", "channel", "source_ip", "client_ip",
        "raw", "observed_host", "confidence", "attribution",
    }


def test_dynamic_token_is_reported_under_its_base(make_service, endpoint):
    service = make_service(telemetry_url=endpoint.url, flush_interval=0.1)
    dns_udp(service.ports["dns_udp"], f"shop0031-9f2c.{ZONE}")
    (event,) = endpoint.wait_for(1, timeout=5)
    assert event["token"] == "shop0031"
    assert "shop0031-9f2c" in event["raw"]


def test_request_without_an_identifier_is_still_reported(make_service, endpoint):
    service = make_service(telemetry_url=endpoint.url, flush_interval=0.1)
    http_request(service.ports["http"], "/", host="127.0.0.1")
    (event,) = endpoint.wait_for(1, timeout=5)
    assert event["token"] == "unidentified"
    assert event["observed_host"] in (None, "127.0.0.1")


def test_platform_traffic_is_marked_by_source_address(make_service, endpoint):
    """No marker header anywhere: a header would be visible to a client through any
    reflection or verbose error, and would show it the shape of the platform."""
    service = make_service(
        telemetry_url=endpoint.url,
        flush_interval=0.1,
        synthetic_networks=(__import__("ipaddress").ip_network("127.0.0.0/8"),),
    )
    dns_udp(service.ports["dns_udp"], f"shop0031.{ZONE}")
    (event,) = endpoint.wait_for(1, timeout=5)
    assert event["synthetic"] is True


def test_traffic_from_elsewhere_is_not_synthetic(make_service, endpoint):
    service = make_service(
        telemetry_url=endpoint.url,
        flush_interval=0.1,
        synthetic_networks=(__import__("ipaddress").ip_network("10.77.0.5/32"),),
    )
    dns_udp(service.ports["dns_udp"], f"shop0031.{ZONE}")
    (event,) = endpoint.wait_for(1, timeout=5)
    assert event["synthetic"] is False


def test_events_are_batched(make_service, endpoint):
    service = make_service(telemetry_url=endpoint.url, flush_interval=0.3)
    for index in range(5):
        dns_udp(service.ports["dns_udp"], f"aaaa000{index}.{ZONE}")
    assert len(endpoint.wait_for(5, timeout=5)) == 5
    assert endpoint.requests < 5  # batched, not one POST per request


def test_listeners_keep_working_when_the_endpoint_is_down(make_service):
    """The point of the bounded queue: a dead endpoint must be invisible to the
    listeners and must never cost a record in the local store."""
    service = make_service(
        telemetry_url=f"http://127.0.0.1:{_free_port()}", flush_interval=0.1
    )
    for index in range(3):
        assert dns_udp(service.ports["dns_udp"], f"aaaa000{index}.{ZONE}")
    http_request(service.ports["http"], "/shop0031", host=ZONE)

    assert len(service.store.wait_for(4, timeout=5)) == 4
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and service.telemetry.stats.failed < 1:
        time.sleep(0.05)
    stats = service.telemetry.stats
    assert stats.enqueued == 4 and stats.failed >= 1 and stats.posted == 0
    assert stats.last_error


def test_a_full_queue_drops_instead_of_blocking(make_service):
    service = make_service(telemetry_url="http://127.0.0.1:1", queue_size=2)
    for _ in range(20):
        service.telemetry.submit({"type": "oob", "app": "x", "token": "t", "channel": "dns"})
    assert service.telemetry.stats.dropped > 0
    dns_udp(service.ports["dns_udp"], f"shop0031.{ZONE}")
    assert service.store.wait_for(1, timeout=5)[0].token == "shop0031"


def test_no_endpoint_configured_is_a_supported_mode(service):
    dns_udp(service.ports["dns_udp"], f"shop0031.{ZONE}")
    assert service.store.wait_for(1, timeout=5)[0].token == "shop0031"
    assert service.telemetry.enabled is False


def test_a_slow_endpoint_does_not_slow_a_listener(make_service):
    """A listener answers in milliseconds even when every request to the endpoint will
    hang: the worker and the flush thread absorb it. A resolver that paused would be both
    a delay in timing-sensitive measurements and a tell."""
    import http.server
    import threading

    class Slow(http.server.BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            time.sleep(3)
            self.send_response(202)
            self.end_headers()

        def do_GET(self):  # noqa: N802
            time.sleep(3)
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Slow)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        service = make_service(
            telemetry_url=f"http://127.0.0.1:{server.server_address[1]}",
            flush_interval=0.05,
            request_timeout=0.2,
        )
        started = time.monotonic()
        for index in range(5):
            dns_udp(service.ports["dns_udp"], f"aaaa000{index}.{ZONE}")
        elapsed = time.monotonic() - started
        assert elapsed < 1.0, f"listeners were dragged down by the endpoint ({elapsed:.2f}s)"
        assert len(service.store.wait_for(5, timeout=5)) == 5
    finally:
        server.shutdown()
        server.server_close()
