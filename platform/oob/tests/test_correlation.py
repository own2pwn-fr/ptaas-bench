"""Attribution: the hint join, the low-confidence fallback, and the unit rules."""

from __future__ import annotations

import time

from edge_resolver.correlation import (
    HIGH,
    LOW,
    MODE_HINT,
    MODE_SOURCE,
    MODE_UNATTRIBUTED,
    NONE,
    CorrelationIndex,
    Hint,
    hosts_match,
)

from conftest import ZONE, dns_udp, http_request


# -- unit: the index ------------------------------------------------------------


def _payload(**overrides) -> dict:
    """A record shaped exactly like the reporting endpoint's registry entries."""
    now = time.time()
    record = {
        "type": "correlation",
        "correlation_id": "c0001",
        "app": "shopfront",
        "signal": "shop.catalog.import.fetch",
        "destination_host": "z9x2k1p8.example-collab.net",
        "route": "/api/admin/imports",
        "param": "source_url",
        "request_id": "req-42",
        "ts": now,
        "client_ip": "10.88.0.7",
        "ttl": 120.0,
        "registered_at": now,
        "expires_at": now + 120.0,
        "synthetic": False,
    }
    record.update(overrides)
    return record


def test_hint_is_parsed_from_the_endpoint_record():
    hint = Hint.from_payload(_payload(), default_ttl=120.0)
    assert hint is not None
    assert (hint.hint_id, hint.app, hint.host) == ("c0001", "shopfront", "z9x2k1p8.example-collab.net")
    assert hint.source_ips == ("10.88.0.7",)
    assert hint.as_attribution()["param"] == "source_url"


def test_duplicate_hints_are_ignored():
    index = CorrelationIndex()
    assert index.add_payloads([_payload(), _payload()]) == 1


def test_host_matching_mirrors_the_endpoint():
    assert hosts_match("a.example.net", "a.example.net")
    assert hosts_match("_x.a.example.net", "a.example.net")  # an intermediate prepended
    assert hosts_match("a.example.net", "_x.a.example.net")  # a wildcard registration
    assert not hosts_match("b.example.net", "a.example.net")
    assert not hosts_match("", "a.example.net")


def test_match_returns_the_registered_route_and_parameter():
    index = CorrelationIndex()
    index.add_payloads([_payload()])
    attribution = index.match("z9x2k1p8.example-collab.net", "10.88.0.7")
    assert (attribution.mode, attribution.confidence, attribution.app) == (MODE_HINT, HIGH, "shopfront")
    assert attribution.as_json()["route"] == "/api/admin/imports"


def test_match_from_another_address_is_downgraded_not_dropped():
    index = CorrelationIndex()
    index.add_payloads([_payload()])
    attribution = index.match("z9x2k1p8.example-collab.net", "10.88.0.9")
    assert attribution.mode == MODE_HINT and attribution.confidence == "medium"


def test_expired_hints_do_not_match():
    index = CorrelationIndex()
    index.add_payloads([_payload(expires_at=time.time() - 1)])
    host = "z9x2k1p8.example-collab.net"
    # From an address we have never seen: nothing at all.
    assert index.match(host, "10.88.0.99").mode == MODE_UNATTRIBUTED
    # From the container that registered it: the strong join is gone with the hint, and
    # only the weak "this application made some outbound request" remains.
    assert index.match(host, "10.88.0.7").mode == MODE_SOURCE


def test_source_fallback_is_low_confidence():
    index = CorrelationIndex()
    index.add_payloads([_payload()])
    attribution = index.match("something-else.example", "10.88.0.7")
    assert (attribution.mode, attribution.confidence, attribution.app) == (MODE_SOURCE, LOW, "shopfront")


def test_stale_source_mapping_expires():
    index = CorrelationIndex(source_ttl=1.0)
    index.add_payloads([_payload(ts=time.time() - 60, registered_at=time.time() - 60)])
    assert index.match("other.example", "10.88.0.7").mode == MODE_UNATTRIBUTED


# -- end to end -----------------------------------------------------------------


def test_dns_lookup_is_attributed_to_the_registered_hint(make_service, endpoint):
    """The whole point of the redesign: a lookup for a host we have never heard of is
    tied back to the route and parameter that caused it."""
    service = make_service(telemetry_url=endpoint.url)
    endpoint.register(
        app="shopfront",
        signal="shop.catalog.import.fetch",
        destination_host="z9x2k1p8.example-collab.net",
        route="/api/admin/imports",
        param="source_url",
        request_id="req-42",
        client_ip="127.0.0.1",
    )
    dns_udp(service.ports["dns_udp"], "z9x2k1p8.example-collab.net")

    (event,) = endpoint.wait_for(1, timeout=5)
    assert event["app"] == "shopfront"
    assert event["confidence"] == HIGH
    assert event["attribution"]["mode"] == MODE_HINT
    assert event["attribution"]["route"] == "/api/admin/imports"
    assert event["attribution"]["param"] == "source_url"
    assert event["attribution"]["correlation_id"]
    assert event["observed_host"] == "z9x2k1p8.example-collab.net"
    # A targeted query, not a stream of the whole pending set.
    assert "z9x2k1p8.example-collab.net" in endpoint.hint_queries


def test_http_callback_after_the_lookup_is_attributed_too(make_service, endpoint):
    service = make_service(telemetry_url=endpoint.url)
    endpoint.register(
        app="shopfront",
        signal="shop.catalog.import.fetch",
        destination_host="z9x2k1p8.example-collab.net",
        route="/api/admin/imports",
        param="source_url",
        client_ip="127.0.0.1",
    )
    http_request(service.ports["http"], "/x", host="z9x2k1p8.example-collab.net")
    (event,) = endpoint.wait_for(1, timeout=5)
    assert (event["channel"], event["app"], event["confidence"]) == ("http", "shopfront", HIGH)


def test_fallback_attribution_is_marked_low_confidence(make_service, endpoint):
    """No hint for this host, but the container has registered hints before, so we can
    say which application made the request -- and must say how sure we are."""
    service = make_service(telemetry_url=endpoint.url)
    endpoint.register(
        app="shopfront",
        signal="shop.catalog.import.fetch",
        destination_host="earlier.example-collab.net",
        route="/api/admin/imports",
        param="source_url",
        client_ip="127.0.0.1",
    )
    # Let the slow listing learn "127.0.0.1 is shopfront".
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and service.index.app_for_source("127.0.0.1") is None:
        time.sleep(0.05)

    dns_udp(service.ports["dns_udp"], "unannounced.example-collab.net")
    (event,) = endpoint.wait_for(1, timeout=5)
    assert event["app"] == "shopfront"
    assert event["confidence"] == LOW
    assert event["attribution"]["mode"] == MODE_SOURCE
    assert event["attribution"].get("route") is None  # no route: that is what low means


def test_unattributable_request_says_so(make_service, endpoint):
    service = make_service(telemetry_url=endpoint.url)
    dns_udp(service.ports["dns_udp"], "nobody-announced-this.example")
    (event,) = endpoint.wait_for(1, timeout=5)
    assert event["confidence"] == NONE
    assert event["attribution"]["mode"] == MODE_UNATTRIBUTED
    assert event["app"] == "edge-resolver"  # never orphaned, but never invented either


def test_a_refusing_endpoint_does_not_break_recording(make_service, endpoint):
    """The registry is part of the endpoint's control surface, which 404s anyone but us.
    If we are ever not the allowlisted address, requests must still be captured."""
    endpoint.control_status = 404
    service = make_service(telemetry_url=endpoint.url)
    dns_udp(service.ports["dns_udp"], "z9x2k1p8.example-collab.net")

    (event,) = endpoint.wait_for(1, timeout=5)
    assert event["confidence"] == NONE
    assert service.store.wait_for(1, timeout=5)[0].host == "z9x2k1p8.example-collab.net"


def test_owned_zone_label_needs_no_hint(make_service, endpoint):
    service = make_service(telemetry_url=endpoint.url)
    dns_udp(service.ports["dns_udp"], f"shop0031.{ZONE}")
    (event,) = endpoint.wait_for(1, timeout=5)
    assert event["token"] == "shop0031"
    assert (event["confidence"], event["attribution"]["mode"]) == (HIGH, "owned_label")
