"""Correlation hints: registration, exposure to the sinkhole, TTL and eviction.

The sinkhole is the resolver for the whole target network, so it captures callbacks
aimed at the tool's own collaborator domain as well as at ours. That is what keeps
blind SSRF, XXE and command injection measurable at all -- but a lookup for
`9f2c.oast.fun` carries nothing tying it to a route or a parameter, so the planted
sink registers the hint here first.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace

from tests.conftest import PLATFORM_IP, TARGET_PEER, client_for, correlation


async def open_run(client, **spec):
    spec.setdefault("tool", "zap")
    return (await client.post("/v1/runs", json=spec)).json()


async def test_registration_returns_the_stored_record(client):
    await open_run(client)
    response = await client.post("/v1/correlations", json=correlation())
    assert response.status_code == 202

    body = response.json()
    assert body["registered"] is True
    entry = body["correlation"]
    assert entry["correlation_id"]
    assert entry["destination_host"] == "9f2c.oast.fun"
    assert entry["signal"] == "shop.import.feed.remote_fetch"
    assert entry["expires_at"] > entry["registered_at"]


async def test_pending_set_is_visible_to_the_sinkhole(client):
    await open_run(client)
    await client.post("/v1/correlations", json=correlation())
    await client.post("/v1/correlations", json=correlation(destination_host="other.example"))

    page = (await client.get("/v1/correlations")).json()
    assert page["count"] == 2
    assert page["ttl"] == 120.0
    assert {entry["destination_host"] for entry in page["correlations"]} == {
        "9f2c.oast.fun",
        "other.example",
    }


async def test_pending_set_filters_by_destination_host(client):
    await open_run(client)
    await client.post("/v1/correlations", json=correlation())
    await client.post("/v1/correlations", json=correlation(destination_host="other.example"))

    matched = (await client.get("/v1/correlations", params={"destination_host": "9f2c.oast.fun"})).json()
    assert matched["count"] == 1
    assert matched["correlations"][0]["param"] == "url"

    # A DNS observation arrives fully qualified and case-folded differently, and an
    # intermediate resolver may prepend a label. Neither may lose the match.
    for observed in ("9F2C.OAST.FUN.", "_dmarc.9f2c.oast.fun"):
        page = (await client.get("/v1/correlations", params={"destination_host": observed})).json()
        assert page["count"] == 1, observed

    assert (await client.get("/v1/correlations", params={"destination_host": "nope.test"})).json()[
        "count"
    ] == 0


async def test_hint_is_exported_with_the_runs_events(client):
    """Scoring must be auditable: a reader sees which hint was live when the
    callback landed, not just that the platform claims they matched."""
    run = await open_run(client)
    registered = (await client.post("/v1/correlations", json=correlation())).json()["correlation"]

    page = (await client.get(f"/v1/runs/{run['run_id']}/events")).json()
    assert len(page["events"]) == 1
    stored = page["events"][0]
    assert stored["type"] == "correlation"
    assert stored["seq"] == 1
    assert stored["correlation_id"] == registered["correlation_id"]
    assert stored["destination_host"] == "9f2c.oast.fun"
    assert stored["route"] == "/api/import/feed"
    assert stored["request_id"] == "req-7781"

    filtered = (
        await client.get(f"/v1/runs/{run['run_id']}/events", params={"type": "correlation"})
    ).json()
    assert len(filtered["events"]) == 1


async def test_hints_share_the_event_sequence(client):
    from tests.conftest import http_request_event

    run = await open_run(client)
    await client.post("/v1/events", json={"events": [http_request_event()]})
    await client.post("/v1/correlations", json=correlation())
    await client.post("/v1/events", json={"events": [http_request_event()]})

    page = (await client.get(f"/v1/runs/{run['run_id']}/events")).json()
    assert [(event["type"], event["seq"]) for event in page["events"]] == [
        ("http_request", 1),
        ("correlation", 2),
        ("http_request", 3),
    ]


async def test_expired_hints_are_evicted(client, collector):
    await open_run(client)
    now = [1_000_000.0]
    collector.correlations.clock = lambda: now[0]

    await client.post("/v1/correlations", json=correlation())
    assert (await client.get("/v1/correlations")).json()["count"] == 1

    now[0] += 119
    assert (await client.get("/v1/correlations")).json()["count"] == 1

    now[0] += 2  # past the 120s TTL
    page = (await client.get("/v1/correlations")).json()
    assert page["count"] == 0
    assert page["correlations"] == []

    stats = (await client.get("/v1/stats")).json()["correlations"]
    assert stats == {"pending": 0, "registered": 1, "expired": 1, "overflowed": 0, "ttl": 120.0}


async def test_expired_hint_survives_in_the_event_stream(client, collector):
    """Eviction is a memory bound, not a retraction: the audit trail must outlive it."""
    run = await open_run(client)
    now = [1_000_000.0]
    collector.correlations.clock = lambda: now[0]
    await client.post("/v1/correlations", json=correlation())

    now[0] += 500
    assert (await client.get("/v1/correlations")).json()["count"] == 0
    page = (await client.get(f"/v1/runs/{run['run_id']}/events")).json()
    assert len(page["events"]) == 1


async def test_per_registration_ttl_overrides_the_default(client, collector):
    await open_run(client)
    now = [1_000_000.0]
    collector.correlations.clock = lambda: now[0]

    await client.post("/v1/correlations", json=correlation(ttl=5))
    await client.post("/v1/correlations", json=correlation(destination_host="slow.example"))

    now[0] += 10
    page = (await client.get("/v1/correlations")).json()
    assert [entry["destination_host"] for entry in page["correlations"]] == ["slow.example"]


async def test_registry_is_bounded(settings):
    """A tool fuzzing an SSRF parameter registers one hint per attempt, thousands per
    minute. An unbounded map would be a memory-exhaustion bug reachable by the
    subject of the benchmark."""
    async with client_for(replace(settings, correlation_max=3)) as (http, collector):
        await http.post("/v1/runs", json={"tool": "zap"})
        for index in range(10):
            await http.post("/v1/correlations", json=correlation(destination_host=f"h{index}.oast.fun"))

        page = (await http.get("/v1/correlations")).json()
        assert page["count"] == 3
        # Oldest first: the hint that has waited longest is the least likely to still
        # be waiting for its callback.
        assert [entry["destination_host"] for entry in page["correlations"]] == [
            "h7.oast.fun",
            "h8.oast.fun",
            "h9.oast.fun",
        ]
        assert collector.correlations.stats()["overflowed"] == 7

        # Every registration is still exported, whatever the live set holds.
        run_id = (await http.get("/v1/runs/active")).json()["run_id"]
        page = (await http.get(f"/v1/runs/{run_id}/events")).json()
        assert len(page["events"]) == 10


async def test_malformed_hint_never_fails_the_caller(client):
    """This runs inside the target's request handling, just before an outbound fetch;
    several oracles are timing-based, so it must not raise and must not stall."""
    await open_run(client)
    for body in (
        {"app": "shopfront"},                                              # no signal, no host
        {"app": "shopfront", "signal": "Bad.Signal", "destination_host": "x.test"},
        {"signal": "shop.import.feed.remote_fetch", "destination_host": "x.test"},  # no app
        "not-an-object",
    ):
        response = await client.post("/v1/correlations", json=body)
        assert response.status_code == 202
        assert response.json() == {"registered": False}

    assert (await client.get("/v1/correlations")).json()["count"] == 0
    assert (await client.get("/v1/stats")).json()["dropped_detail"]["invalid"] == 4


async def test_hint_batched_through_the_event_stream_also_registers(client):
    """An SDK may prefer to piggyback the hint on its ordinary telemetry; the
    sinkhole must see it either way."""
    run = await open_run(client)
    response = await client.post(
        "/v1/events", json={"events": [dict(correlation(), type="correlation")]}
    )
    assert response.json()["accepted"] == 1

    assert (await client.get("/v1/correlations")).json()["count"] == 1
    page = (await client.get(f"/v1/runs/{run['run_id']}/events")).json()
    assert page["events"][0]["type"] == "correlation"
    assert page["events"][0]["correlation_id"]


async def test_hints_register_while_idle_but_are_not_stored(client):
    """Targets stay instrumented between runs and the sinkhole must behave the same
    way whether or not a benchmark is in progress -- an observable difference is a
    tell to anything watching the network."""
    response = await client.post("/v1/correlations", json=correlation())
    assert response.status_code == 202
    assert response.json()["registered"] is True
    assert (await client.get("/v1/correlations")).json()["count"] == 1

    stats = (await client.get("/v1/stats")).json()
    assert stats["events_total"] == 0
    assert stats["discarded_idle"] == 1


async def test_platform_hints_are_marked_synthetic(client):
    run = await open_run(client)
    await client.post("/v1/correlations", json=correlation(peer_ip=PLATFORM_IP))

    stored = (await client.get(f"/v1/runs/{run['run_id']}/events")).json()["events"][0]
    assert stored["synthetic"] is True


# ------------------------------------------------- the hint and the callback race


async def test_sinkhole_can_wait_for_a_hint_that_has_not_arrived_yet(client):
    """The SDK dispatches each hint immediately on its own connection because the DNS
    lookup follows within microseconds; either can win. A sinkhole that observed the
    callback first must be able to complete the join rather than conclude the callback
    was unattributable -- that failure mode would show up as blind vulnerabilities
    scoring as missed by every tool."""
    await open_run(client)

    waiter = asyncio.create_task(
        client.get(
            "/v1/correlations", params={"destination_host": "9f2c.oast.fun", "wait_ms": 2000}
        )
    )
    await asyncio.sleep(0.05)  # the lookup lost the race: nothing is registered yet
    assert not waiter.done()

    await client.post("/v1/correlations", json=correlation())
    page = (await waiter).json()
    assert page["count"] == 1
    assert page["correlations"][0]["destination_host"] == "9f2c.oast.fun"


async def test_waiting_is_bounded_and_returns_empty(client):
    await open_run(client)
    page = (
        await client.get("/v1/correlations", params={"destination_host": "nope.test", "wait_ms": 60})
    ).json()
    assert page["count"] == 0


async def test_waiting_is_not_woken_by_an_unrelated_hint(client):
    await open_run(client)
    waiter = asyncio.create_task(
        client.get("/v1/correlations", params={"destination_host": "wanted.test", "wait_ms": 1500})
    )
    await asyncio.sleep(0.05)

    await client.post("/v1/correlations", json=correlation(destination_host="other.test"))
    await asyncio.sleep(0.05)
    assert not waiter.done()

    await client.post("/v1/correlations", json=correlation(destination_host="wanted.test"))
    page = (await waiter).json()
    assert [entry["destination_host"] for entry in page["correlations"]] == ["wanted.test"]


async def test_a_hint_that_arrives_first_needs_no_waiting(client):
    await open_run(client)
    await client.post("/v1/correlations", json=correlation())
    page = (
        await client.get(
            "/v1/correlations", params={"destination_host": "9f2c.oast.fun", "wait_ms": 5000}
        )
    ).json()
    assert page["count"] == 1


async def test_both_sides_land_in_the_event_stream_whatever_the_order(client):
    """The live pending set is an optimisation. The system of record is the event
    stream, where the hint and the sinkhole's observation each carry their own seq, so
    the authoritative join is offline and order-independent -- a live miss costs
    nothing."""
    run = await open_run(client)

    # Deliberately the losing order: the callback is reported before the hint exists.
    await client.post(
        "/v1/events",
        json={
            "events": [
                {
                    "type": "oob",
                    "app": "shopfront",
                    "token": "unknown",
                    "channel": "dns",
                    "source_ip": "10.88.0.9",
                    "raw": "9f2c.oast.fun A",
                }
            ]
        },
    )
    await client.post("/v1/correlations", json=correlation())

    events = (await client.get(f"/v1/runs/{run['run_id']}/events")).json()["events"]
    assert [(event["type"], event["seq"]) for event in events] == [("oob", 1), ("correlation", 2)]
    observed, hint = events
    assert hint["destination_host"] in observed["raw"]


# ----------------------------------------------- container-to-app mapping stamp


async def test_registration_peer_is_stamped_when_the_sink_omits_it(settings):
    """The sinkhole's fallback attribution tier needs to know which container speaks
    for which app; without it, its strongest rule's source check degrades to
    "unknown"."""
    async with client_for(settings, peer=TARGET_PEER) as (http, _):
        await http.post("/v1/runs", json={"tool": "zap"})
        entry = (await http.post("/v1/correlations", json=correlation())).json()["correlation"]
        assert entry["client_ip"] == TARGET_PEER
        assert entry["app"] == "shopfront"

        listed = (await http.get("/v1/correlations")).json()["correlations"]
        assert listed[0]["client_ip"] == TARGET_PEER


async def test_a_sink_supplied_address_is_not_overwritten(settings):
    async with client_for(settings, peer=TARGET_PEER) as (http, _):
        await http.post("/v1/runs", json={"tool": "zap"})
        entry = (
            await http.post("/v1/correlations", json=correlation(client_ip="203.0.113.9"))
        ).json()["correlation"]
        assert entry["client_ip"] == "203.0.113.9"


async def test_hints_batched_through_the_event_stream_are_stamped_too(settings):
    async with client_for(settings, peer=TARGET_PEER) as (http, _):
        await http.post("/v1/runs", json={"tool": "zap"})
        await http.post(
            "/v1/events", json={"events": [dict(correlation(), type="correlation")]}
        )
        assert (await http.get("/v1/correlations")).json()["correlations"][0]["client_ip"] == TARGET_PEER


async def test_the_stamp_is_recorded_but_never_decides_anything(settings):
    """`client_ip` stays descriptive even when the collector wrote it itself: the
    synthetic rule reads peer_ip and source_ip, and nothing else."""
    async with client_for(settings, peer=PLATFORM_IP) as (http, _):
        run = (await http.post("/v1/runs", json={"tool": "zap"})).json()
        await http.post("/v1/correlations", json=correlation())

        stored = (await http.get(f"/v1/runs/{run['run_id']}/events")).json()["events"][0]
        assert stored["client_ip"] == PLATFORM_IP
        assert stored["synthetic"] is False


async def test_only_correlations_are_stamped(settings):
    """On an http_request, `client_ip` means the client of the target. Filling it from
    the SDK's own connection would assert that the target called itself."""
    from tests.conftest import http_request_event

    async with client_for(settings, peer=TARGET_PEER) as (http, _):
        run = (await http.post("/v1/runs", json={"tool": "zap"})).json()
        await http.post("/v1/events", json={"events": [http_request_event()]})

        stored = (await http.get(f"/v1/runs/{run['run_id']}/events")).json()["events"][0]
        assert stored["client_ip"] is None


async def test_registered_after_is_an_incremental_cursor(client):
    """The sinkhole polls on a timer to keep its map warm, not to answer a specific
    callback, so it should not have to re-read the whole set."""
    await open_run(client)
    await client.post("/v1/correlations", json=correlation(destination_host="first.test"))
    page = (await client.get("/v1/correlations")).json()
    cursor = page["correlations"][0]["registered_at"]

    assert (await client.get("/v1/correlations", params={"registered_after": cursor})).json()["count"] == 0

    await client.post("/v1/correlations", json=correlation(destination_host="second.test"))
    page = (await client.get("/v1/correlations", params={"registered_after": cursor})).json()
    assert [entry["destination_host"] for entry in page["correlations"]] == ["second.test"]


async def test_one_hint_serves_several_observations(client):
    """No consume/ack by design: a single outbound fetch is legitimately observed as a
    DNS lookup, then an HTTP connection, then perhaps SMTP. Retiring the hint on first
    match would destroy the rest of that evidence."""
    await open_run(client)
    await client.post("/v1/correlations", json=correlation())

    for _ in range(3):
        page = (
            await client.get("/v1/correlations", params={"destination_host": "9f2c.oast.fun"})
        ).json()
        assert page["count"] == 1
