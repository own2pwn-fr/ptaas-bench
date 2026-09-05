"""Correlation hints: registration, exposure to the sinkhole, TTL and eviction.

The sinkhole is the resolver for the whole target network, so it captures callbacks
aimed at the tool's own collaborator domain as well as at ours. That is what keeps
blind SSRF, XXE and command injection measurable at all -- but a lookup for
`9f2c.oast.fun` carries nothing tying it to a route or a parameter, so the planted
sink registers the hint here first.
"""

from __future__ import annotations

from dataclasses import replace

from tests.conftest import PLATFORM_IP, client_for, correlation


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
    await client.post("/v1/correlations", json=correlation(client_ip=PLATFORM_IP))

    stored = (await client.get(f"/v1/runs/{run['run_id']}/events")).json()["events"][0]
    assert stored["synthetic"] is True
