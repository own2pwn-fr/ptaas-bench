"""Admin API: paging, reset, stats, and who is allowed to read any of it."""

from __future__ import annotations

import ipaddress

from conftest import ZONE, admin_get, dns_udp, http_request


def test_observations_are_paged_by_sequence(service):
    dns_udp(service.ports["dns_udp"], f"aaaa0001.{ZONE}")
    service.store.wait_for(1, timeout=5)
    first = admin_get(service.ports["admin"], "/observations?since=0")
    assert first["count"] == 1
    assert first["observations"][0]["token"] == "aaaa0001"
    cursor = first["next_seq"]

    http_request(service.ports["http"], "/", host=f"bbbb0002.{ZONE}")
    service.store.wait_for(2, timeout=5)
    second = admin_get(service.ports["admin"], f"/observations?since={cursor}")
    assert [c["token"] for c in second["observations"]] == ["bbbb0002"]
    assert second["next_seq"] == cursor + 1


def test_callbacks_path_is_kept_as_an_alias(service):
    dns_udp(service.ports["dns_udp"], f"aaaa0001.{ZONE}")
    service.store.wait_for(1, timeout=5)
    page = admin_get(service.ports["admin"], "/callbacks?since=0")
    assert page["callbacks"] == page["observations"] and page["count"] == 1


def test_limit_is_honoured(service):
    for index in range(3):
        dns_udp(service.ports["dns_udp"], f"aaaa000{index}.{ZONE}")
    service.store.wait_for(3, timeout=5)
    page = admin_get(service.ports["admin"], "/observations?since=0&limit=2")
    assert page["count"] == 2 and page["last_seq"] == 3


def test_reset_clears_the_store_and_the_sequence(service):
    dns_udp(service.ports["dns_udp"], f"aaaa0001.{ZONE}")
    service.store.wait_for(1, timeout=5)
    assert admin_get(service.ports["admin"], "/reset", method="POST") == {"ok": True}

    empty = admin_get(service.ports["admin"], "/observations?since=0")
    assert empty["count"] == 0 and empty["last_seq"] == 0

    dns_udp(service.ports["dns_udp"], f"bbbb0002.{ZONE}")
    service.store.wait_for(1, timeout=5)
    after = admin_get(service.ports["admin"], "/observations?since=0")
    assert [c["seq"] for c in after["observations"]] == [1]


def test_stats_reports_the_moving_parts(service):
    dns_udp(service.ports["dns_udp"], f"aaaa0001.{ZONE}")
    service.store.wait_for(1, timeout=5)
    stats = admin_get(service.ports["admin"], "/stats")
    assert stats["zone"] == ZONE and stats["stored"] == 1
    assert stats["telemetry"]["enqueued"] == 1
    assert set(stats["correlation"]) == {"pending", "received", "sources"}
    assert stats["attribution"] is not None


def test_hints_can_be_registered_locally(service):
    """Used by the platform's self-tests, which drive both halves of a join without
    standing up the reporting endpoint."""
    added = admin_get(service.ports["admin"], "/hints", method="POST")
    assert added["added"] == 0  # no body: nothing to add, and no error either


def test_long_poll_returns_when_an_observation_lands(service):
    import threading

    dns_udp(service.ports["dns_udp"], f"aaaa0001.{ZONE}")
    service.store.wait_for(1, timeout=5)
    timer = threading.Timer(0.3, lambda: dns_udp(service.ports["dns_udp"], f"bbbb0002.{ZONE}"))
    timer.start()
    try:
        page = admin_get(service.ports["admin"], "/observations?since=1&wait=5")
    finally:
        timer.cancel()
    assert [c["token"] for c in page["observations"]] == ["bbbb0002"]


def test_unknown_route_is_404(service):
    assert admin_get(service.ports["admin"], "/nope")["error"] == "not found"


def test_the_admin_api_records_nothing_itself(service):
    admin_get(service.ports["admin"], "/observations?since=0")
    assert len(service.store) == 0


def test_a_caller_outside_the_allowlist_gets_the_same_404(make_service):
    """Applications share the internal network, and an application is what a client takes
    control of first, so binding there is not by itself a boundary. A refused caller must
    not be able to tell that anything else exists."""
    service = make_service(admin_networks=(ipaddress.ip_network("10.77.0.4/32"),))
    body = admin_get(service.ports["admin"], "/observations?since=0")
    assert body == {"error": "not found"}
    body = admin_get(service.ports["admin"], "/stats")
    assert body == {"error": "not found"}
