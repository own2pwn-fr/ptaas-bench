"""Control API on the internal interface: paging, reset, stats."""

from __future__ import annotations

from conftest import ZONE, control_get, dns_udp, http_request


def test_callbacks_are_paged_by_sequence(service):
    dns_udp(service.ports["dns_udp"], f"aaaa0001.{ZONE}")
    service.store.wait_for(1, timeout=5)
    first = control_get(service.ports["control"], "/callbacks?since=0")
    assert first["count"] == 1
    assert first["callbacks"][0]["token"] == "aaaa0001"
    cursor = first["next_seq"]

    http_request(service.ports["http"], "/", host=f"bbbb0002.{ZONE}")
    service.store.wait_for(2, timeout=5)
    second = control_get(service.ports["control"], f"/callbacks?since={cursor}")
    assert [c["token"] for c in second["callbacks"]] == ["bbbb0002"]
    assert second["next_seq"] == cursor + 1


def test_limit_is_honoured(service):
    for index in range(3):
        dns_udp(service.ports["dns_udp"], f"aaaa000{index}.{ZONE}")
    service.store.wait_for(3, timeout=5)
    page = control_get(service.ports["control"], "/callbacks?since=0&limit=2")
    assert page["count"] == 2 and page["last_seq"] == 3


def test_reset_clears_the_store_and_the_sequence(service):
    dns_udp(service.ports["dns_udp"], f"aaaa0001.{ZONE}")
    service.store.wait_for(1, timeout=5)
    assert control_get(service.ports["control"], "/reset", method="POST") == {"ok": True}

    empty = control_get(service.ports["control"], "/callbacks?since=0")
    assert empty["count"] == 0 and empty["last_seq"] == 0

    # And the sequence restarts, so a self-test can always page from since=0.
    dns_udp(service.ports["dns_udp"], f"bbbb0002.{ZONE}")
    service.store.wait_for(1, timeout=5)
    after = control_get(service.ports["control"], "/callbacks?since=0")
    assert [c["seq"] for c in after["callbacks"]] == [1]


def test_stats_reports_the_collector_state(service):
    dns_udp(service.ports["dns_udp"], f"aaaa0001.{ZONE}")
    service.store.wait_for(1, timeout=5)
    stats = control_get(service.ports["control"], "/stats")
    assert stats["domain"] == ZONE and stats["stored"] == 1
    assert stats["collector"]["enqueued"] == 1


def test_long_poll_returns_when_a_callback_lands(service):
    import threading

    dns_udp(service.ports["dns_udp"], f"aaaa0001.{ZONE}")
    service.store.wait_for(1, timeout=5)
    timer = threading.Timer(
        0.3, lambda: dns_udp(service.ports["dns_udp"], f"bbbb0002.{ZONE}")
    )
    timer.start()
    try:
        page = control_get(service.ports["control"], "/callbacks?since=1&wait=5")
    finally:
        timer.cancel()
    assert [c["token"] for c in page["callbacks"]] == ["bbbb0002"]


def test_unknown_route_is_404(service):
    assert control_get(service.ports["control"], "/nope")["error"] == "not found"


def test_control_api_does_not_record_itself(service):
    control_get(service.ports["control"], "/callbacks?since=0")
    assert len(service.store) == 0
