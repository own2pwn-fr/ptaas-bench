"""Ingestion semantics: always 202, stamped with the active run, ordered by seq."""

from __future__ import annotations

from httpx import ASGITransport, AsyncClient

from bench_collector.app import create_app
from bench_collector.ingest import Collector
from tests.conftest import http_request_event, trigger_event


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
    assert page["events"][1]["vuln_id"] == "BENCH-SHOP-0001"
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

    restarted = Collector(settings)
    app = create_app(restarted)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://collector:8900") as http,
    ):
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
