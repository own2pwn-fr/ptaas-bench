"""Ingestion semantics: always 202, stamped with the active run, ordered by seq."""

from __future__ import annotations

import json

from tests.conftest import (
    PLATFORM_IP,
    TOOL_IP,
    client_for,
    http_request_event,
    trigger_event,
)
from tests.conftest import correlation as correlation_hint


async def post_events(client, events):
    return await client.post("/v1/events", json={"events": events})


async def open_run(client, **spec):
    spec.setdefault("tool", "zap")
    return (await client.post("/v1/runs", json=spec)).json()


async def test_events_are_accepted_and_discarded_while_idle(client):
    response = await post_events(client, [http_request_event()])
    assert response.status_code == 202
    assert response.json()["discarded_idle"] == 1

    stats = (await client.get("/v1/stats")).json()
    assert stats["active_run"] is None
    assert stats["events_total"] == 0
    assert stats["discarded_idle"] == 1
    assert stats["dropped"] == 0


async def test_events_are_stamped_with_the_active_run(client):
    run = await open_run(client, targets=["shopfront"])
    assert (await post_events(client, [http_request_event(), trigger_event()])).status_code == 202

    page = (await client.get(f"/v1/runs/{run['run_id']}/events")).json()
    assert page["run_id"] == run["run_id"]
    assert [event["type"] for event in page["events"]] == ["http_request", "trigger"]
    assert all(event["received_at"] for event in page["events"])
    # The parameter location keeps its wire name, not the python-safe alias.
    assert page["events"][0]["params"][0]["in"] == "path"
    assert page["events"][1]["signal"] == "shop.catalog.query.plan_anomaly"
    assert page["events"][1]["evidence"]["payload"] == "' OR 1=1--"

    listed = {run_row["run_id"]: run_row for run_row in (await client.get("/v1/runs")).json()}
    assert listed[run["run_id"]]["event_count"] == 2


async def test_events_after_close_are_discarded(client):
    run = await open_run(client)
    await post_events(client, [http_request_event()])
    await client.post(f"/v1/runs/{run['run_id']}/close")

    assert (await post_events(client, [http_request_event()])).status_code == 202
    page = (await client.get(f"/v1/runs/{run['run_id']}/events")).json()
    assert len(page["events"]) == 1


async def test_seq_is_monotonic_and_pagination_walks_it(client):
    run = await open_run(client)
    for index in range(25):
        await post_events(client, [http_request_event(path=f"/api/orders/{index}")])

    page = (await client.get(f"/v1/runs/{run['run_id']}/events", params={"limit": 10})).json()
    assert [event["seq"] for event in page["events"]] == list(range(1, 11))
    assert page["next_seq"] == 10

    seen = list(page["events"])
    cursor = page["next_seq"]
    while cursor is not None:
        page = (
            await client.get(
                f"/v1/runs/{run['run_id']}/events", params={"limit": 10, "after_seq": cursor}
            )
        ).json()
        seen.extend(page["events"])
        cursor = page["next_seq"]

    assert [event["seq"] for event in seen] == list(range(1, 26))
    assert [event["path"] for event in seen] == [f"/api/orders/{index}" for index in range(25)]


async def test_seq_restarts_per_run(client):
    first = await open_run(client)
    await post_events(client, [http_request_event()])
    second = await open_run(client, tool="burp", force=True)
    await post_events(client, [http_request_event()])

    first_page = (await client.get(f"/v1/runs/{first['run_id']}/events")).json()
    second_page = (await client.get(f"/v1/runs/{second['run_id']}/events")).json()
    assert [event["seq"] for event in first_page["events"]] == [1]
    assert [event["seq"] for event in second_page["events"]] == [1]


async def test_type_filter(client):
    run = await open_run(client)
    await post_events(
        client,
        [
            http_request_event(),
            trigger_event(),
            {"type": "oob", "app": "shopfront", "token": "c0ffee", "channel": "dns", "source_ip": "10.0.0.9"},
            {"type": "note", "app": "shopfront", "message": "seeded"},
        ],
    )

    page = (await client.get(f"/v1/runs/{run['run_id']}/events", params={"type": "oob"})).json()
    assert len(page["events"]) == 1
    assert page["events"][0]["token"] == "c0ffee"
    assert page["events"][0]["channel"] == "dns"
    # Filtering must not renumber: seq stays the run-wide cursor.
    assert page["events"][0]["seq"] == 3


async def test_malformed_events_are_dropped_not_fatal(client):
    run = await open_run(client)
    response = await post_events(
        client,
        [
            http_request_event(),
            {"type": "trigger", "app": "shopfront", "vuln_id": "not-a-bench-id"},  # bad pattern
            {"type": "http_request", "method": "GET"},  # missing app and route
            {"type": "telepathy", "app": "shopfront"},  # unknown discriminator
            {"type": "oob", "app": "shopfront", "token": "x", "channel": "carrier-pigeon"},  # bad enum
            trigger_event(),
        ],
    )
    assert response.status_code == 202
    assert response.json() == {"accepted": 2, "dropped": 4, "discarded_idle": 0}

    page = (await client.get(f"/v1/runs/{run['run_id']}/events")).json()
    assert [event["type"] for event in page["events"]] == ["http_request", "trigger"]
    assert [event["seq"] for event in page["events"]] == [1, 2]

    stats = (await client.get("/v1/stats")).json()
    assert stats["dropped"] == 4
    assert stats["dropped_detail"]["invalid"] == 4


async def test_broken_body_never_5xxes(client):
    await open_run(client)
    for body in (b"not json at all", b'{"events": "nope"}', b'{"nope": 1}', b""):
        response = await client.post(
            "/v1/events", content=body, headers={"content-type": "application/json"}
        )
        assert response.status_code == 202
    assert (await client.get("/v1/stats")).json()["events_total"] == 0


async def test_synthetic_flag_is_preserved(client):
    run = await open_run(client)
    await post_events(
        client,
        [http_request_event(synthetic=True), http_request_event(), trigger_event(synthetic=True)],
    )

    page = (await client.get(f"/v1/runs/{run['run_id']}/events")).json()
    assert [event["synthetic"] for event in page["events"]] == [True, False, True]
    # Stored, never silently filtered: the scorer excludes them, the collector does not.
    assert (await client.get("/v1/stats")).json()["synthetic_events"] == 2


async def test_batch_of_500(client):
    run = await open_run(client)
    batch = [http_request_event(path=f"/api/orders/{index}") for index in range(500)]
    response = await post_events(client, batch)
    assert response.status_code == 202
    assert response.json()["accepted"] == 500

    page = (await client.get(f"/v1/runs/{run['run_id']}/events", params={"limit": 50000})).json()
    assert len(page["events"]) == 500
    assert [event["seq"] for event in page["events"]] == list(range(1, 501))
    assert page["next_seq"] == 500
    assert (await client.get(f"/v1/runs/{run['run_id']}/events", params={"after_seq": 500})).json()[
        "next_seq"
    ] is None


async def test_batch_over_the_documented_maximum_is_truncated(client):
    run = await open_run(client)
    response = await post_events(client, [http_request_event() for _ in range(505)])
    assert response.status_code == 202
    assert response.json() == {"accepted": 500, "dropped": 5, "discarded_idle": 0}

    page = (await client.get(f"/v1/runs/{run['run_id']}/events", params={"limit": 50000})).json()
    assert len(page["events"]) == 500
    assert (await client.get("/v1/stats")).json()["dropped_detail"]["over_batch"] == 5


async def test_oversized_strings_are_clipped_rather_than_dropped(client):
    """A trigger is proof of exploitation; never lose one over a length budget."""
    run = await open_run(client)
    await post_events(client, [trigger_event(evidence={"payload": "A" * 4000, "detail": "B" * 4000})])

    page = (await client.get(f"/v1/runs/{run['run_id']}/events")).json()
    assert len(page["events"]) == 1
    assert page["events"][0]["evidence"]["payload"] == "A" * 1024
    assert page["events"][0]["evidence"]["detail"] == "B" * 1024


async def test_unknown_sdk_fields_survive(client):
    run = await open_run(client)
    await post_events(client, [http_request_event(tenant="acme", body_bytes=17)])

    stored = (await client.get(f"/v1/runs/{run['run_id']}/events")).json()["events"][0]
    assert stored["tenant"] == "acme"
    assert stored["body_bytes"] == 17


async def test_stats_counts_by_type(client):
    await open_run(client)
    await post_events(
        client,
        [
            http_request_event(),
            http_request_event(),
            trigger_event(),
            {"type": "note", "app": "shopfront", "message": "hello"},
        ],
    )
    stats = (await client.get("/v1/stats")).json()
    assert stats["events_by_type"] == {"http_request": 2, "trigger": 1, "note": 1}
    assert stats["events_total"] == 4
    assert stats["active_run"] is not None


async def test_active_run_and_seq_survive_a_collector_restart(client, collector, settings):
    """Targets never send a run id, so the in-memory pointer must be re-adopted from
    the database; otherwise a collector restart mid-scan silently discards the rest
    of the run as "idle" and the tool is scored on half its work."""
    run = await open_run(client)
    await post_events(client, [http_request_event(), http_request_event()])
    await client.get("/v1/stats")  # forces a flush

    async with client_for(settings) as (http, _restarted):
        active = await http.get("/v1/runs/active")
        assert active.status_code == 200
        assert active.json()["run_id"] == run["run_id"]

        await http.post("/v1/events", json={"events": [http_request_event()]})
        page = (await http.get(f"/v1/runs/{run['run_id']}/events")).json()
        assert [event["seq"] for event in page["events"]] == [1, 2, 3]


async def test_openapi_schema_is_generated(client):
    """The published spec is the contract; a broken openapi_extra breaks the docs."""
    response = await client.get("/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]
    assert set(paths) >= {
        "/healthz",
        "/v1/runs",
        "/v1/runs/active",
        "/v1/runs/{run_id}/close",
        "/v1/runs/{run_id}/events",
        "/v1/events",
        "/v1/stats",
    }


# --------------------------------------------------------------------- signals


async def test_trigger_identified_by_signal_alone(client):
    """Current targets emit an opaque metric-shaped signal, never a catalog id."""
    run = await open_run(client)
    await post_events(client, [trigger_event(signal="shop.checkout.coupon.negative_total")])

    stored = (await client.get(f"/v1/runs/{run['run_id']}/events")).json()["events"][0]
    assert stored["signal"] == "shop.checkout.coupon.negative_total"
    assert stored["vuln_id"] is None


async def test_trigger_identified_by_vuln_id_alone_still_works(client):
    """Older targets have not been re-signalled yet; their runs must stay scoreable."""
    run = await open_run(client)
    await post_events(client, [trigger_event(signal=None, vuln_id="BENCH-SHOP-0001")])

    stored = (await client.get(f"/v1/runs/{run['run_id']}/events")).json()["events"][0]
    assert stored["vuln_id"] == "BENCH-SHOP-0001"
    assert stored["signal"] is None


async def test_trigger_may_carry_both(client):
    run = await open_run(client)
    await post_events(client, [trigger_event(vuln_id="BENCH-SHOP-0001")])

    stored = (await client.get(f"/v1/runs/{run['run_id']}/events")).json()["events"][0]
    assert stored["vuln_id"] == "BENCH-SHOP-0001"
    assert stored["signal"] == "shop.catalog.query.plan_anomaly"


async def test_trigger_without_any_identifier_is_dropped(client):
    """Unattributable is worse than absent: it would skew ground truth silently."""
    run = await open_run(client)
    response = await post_events(client, [trigger_event(signal=None), trigger_event()])
    assert response.json() == {"accepted": 1, "dropped": 1, "discarded_idle": 0}

    page = (await client.get(f"/v1/runs/{run['run_id']}/events")).json()
    assert len(page["events"]) == 1


async def test_malformed_signals_are_dropped(client):
    run = await open_run(client)
    bad = [
        "Shop.Catalog.Query",       # upper case
        "shop.catalog",            # too few segments
        "shop..query.anomaly",     # empty segment
        "1shop.catalog.anomaly",   # leading digit
        "shop.catalog.query-plan", # hyphen is not allowed inside a segment
    ]
    response = await post_events(client, [trigger_event(signal=signal) for signal in bad])
    assert response.json()["dropped"] == len(bad)
    assert (await client.get(f"/v1/runs/{run['run_id']}/events")).json()["events"] == []


async def test_signals_are_stored_verbatim_and_never_resolved(client, collector):
    """The collector does not read the catalog. Signal -> vulnerability is the
    scorer's job, and keeping the answer key out of this process is half the reason
    the network split exists."""
    run = await open_run(client)
    await post_events(client, [trigger_event(signal="edge.session.cookie.replay_accepted")])

    stored = (await client.get(f"/v1/runs/{run['run_id']}/events")).json()["events"][0]
    assert stored["signal"] == "edge.session.cookie.replay_accepted"
    assert "BENCH-" not in json.dumps(stored)


# ------------------------------------------------------------- /v1/traces alias


async def test_traces_is_an_alias_of_events(client):
    """A target pointed at an OTLP-ish path is unremarkable; one pointed at a
    collector named after the benchmark tells the tool what it is inside of."""
    run = await open_run(client)
    response = await client.post("/v1/traces", json={"events": [http_request_event(), trigger_event()]})
    assert response.status_code == 202
    assert response.json()["accepted"] == 2

    page = (await client.get(f"/v1/runs/{run['run_id']}/events")).json()
    assert [event["type"] for event in page["events"]] == ["http_request", "trigger"]


async def test_traces_and_events_share_one_sequence(client):
    run = await open_run(client)
    await client.post("/v1/traces", json={"events": [http_request_event()]})
    await client.post("/v1/events", json={"events": [http_request_event()]})
    await client.post("/v1/traces", json={"events": [http_request_event()]})

    page = (await client.get(f"/v1/runs/{run['run_id']}/events")).json()
    assert [event["seq"] for event in page["events"]] == [1, 2, 3]


async def test_traces_never_fails_the_caller(client):
    await open_run(client)
    response = await client.post(
        "/v1/traces", content=b"{ not json", headers={"content-type": "application/json"}
    )
    assert response.status_code == 202


# ------------------------------------------------------ synthetic by source address


async def test_platform_traffic_is_marked_synthetic_by_source(client):
    """The selftest header is gone platform-wide: any reflection or header-injection
    flaw would have shown a tool the shape of the grader."""
    run = await open_run(client)
    await post_events(
        client,
        [
            http_request_event(client_ip=PLATFORM_IP),
            http_request_event(client_ip=TOOL_IP),
            http_request_event(client_ip=f"{PLATFORM_IP}:51234"),
            trigger_event(client_ip=PLATFORM_IP),
            {"type": "oob", "app": "edge", "token": "abc", "channel": "dns", "source_ip": PLATFORM_IP},
        ],
    )

    events = (await client.get(f"/v1/runs/{run['run_id']}/events")).json()["events"]
    assert [event["synthetic"] for event in events] == [True, False, True, True, True]
    stats = (await client.get("/v1/stats")).json()
    assert stats["synthetic_by_source"] == 4
    assert stats["synthetic_cidrs"] == ["10.99.0.0/16", "fd00:99::/32"]


async def test_sdk_flag_still_overrides(client):
    """An SDK that knows better wins: it may see platform traffic arriving through a
    proxy, where the address we get is the proxy's."""
    run = await open_run(client)
    await post_events(client, [http_request_event(client_ip=TOOL_IP, synthetic=True)])

    events = (await client.get(f"/v1/runs/{run['run_id']}/events")).json()["events"]
    assert events[0]["synthetic"] is True
    assert (await client.get("/v1/stats")).json()["synthetic_by_source"] == 0


async def test_unparsable_or_absent_addresses_are_not_synthetic(client):
    run = await open_run(client)
    await post_events(
        client,
        [
            http_request_event(),
            http_request_event(client_ip=""),
            http_request_event(client_ip="not-an-address"),
            http_request_event(client_ip="10.99.4.12, 192.0.2.1"),
        ],
    )
    events = (await client.get(f"/v1/runs/{run['run_id']}/events")).json()["events"]
    # Only the forwarded-for list resolves: its first hop is the original client.
    assert [event["synthetic"] for event in events] == [False, False, False, True]


async def test_no_cidrs_configured_means_nothing_is_synthetic_by_source(settings):
    from dataclasses import replace

    async with client_for(replace(settings, synthetic_networks=())) as (http, _):
        await http.post("/v1/runs", json={"tool": "zap"})
        await http.post("/v1/events", json={"events": [http_request_event(client_ip=PLATFORM_IP)]})
        stats = (await http.get("/v1/stats")).json()
        assert stats["synthetic_events"] == 0
        assert stats["synthetic_cidrs"] == []


# ------------------------------------------------------------------- deception


async def test_api_description_is_not_published_by_default(settings):
    """Targets are dual-homed, so a tool with RCE on one can reach this service. A
    served schema listing runs and events would be a complete confession."""
    from dataclasses import replace

    async with client_for(replace(settings, expose_schema=False)) as (http, _):
        assert (await http.get("/openapi.json")).status_code == 404
        assert (await http.get("/docs")).status_code == 404
        assert (await http.get("/healthz")).status_code == 200


async def test_service_presents_itself_as_ordinary_telemetry(client):
    schema = (await client.get("/openapi.json")).json()
    title = schema["info"]["title"].lower()
    assert "bench" not in title and "ptaas" not in title


async def test_control_surface_can_be_limited_by_source_address(settings):
    """A tool with RCE on a target reaches this port at the address in the target's
    environment; the network split cannot help there. The export that lists which
    planted sinks fired must not answer it."""
    from dataclasses import replace

    from bench_collector.config import parse_cidrs

    # httpx's ASGI transport presents 127.0.0.1, so allowing a foreign range makes
    # this client the unauthorised one.
    async with client_for(replace(settings, control_networks=parse_cidrs("10.99.0.0/16"))) as (http, _):
        for path in ("/v1/runs", "/v1/runs/active", "/v1/stats", "/v1/correlations"):
            assert (await http.get(path)).status_code == 404, path
        assert (await http.post("/v1/runs", json={"tool": "zap"})).status_code == 404

        # Instrumentation still answers identically: a target must not be able to
        # tell that the collector is guarded, or that a run is in progress.
        assert (await http.get("/healthz")).status_code == 200
        assert (await http.post("/v1/events", json={"events": [http_request_event()]})).status_code == 202
        assert (await http.post("/v1/traces", json={"events": [http_request_event()]})).status_code == 202
        assert (await http.post("/v1/correlations", json=correlation_hint())).status_code == 202


async def test_control_surface_is_open_when_unconfigured(client):
    assert (await client.get("/v1/stats")).status_code == 200
