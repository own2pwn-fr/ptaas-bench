"""The run's own record: what was running, where, and whether it was reset.

A published score has to be re-runnable and contestable from its own record. The
event stream alone cannot do that: it says what happened, not which container held
which address at the time, which image digest was actually running, or whether the
target came back to its seeded state afterwards.
"""

from __future__ import annotations

import logging

from tests.conftest import client_for, http_request_event

SHOPFRONT = {
    "service": "shopfront-web",
    "container_id": "9c1f0e2b7a44",
    "addresses": ["10.77.0.21", "10.88.0.31"],
    "image_digest": "sha256:2b1f...c9",
    "state_digest_before": "seed-7f31a0",
}
EDGE = {
    "service": "edge-nginx",
    "container_id": "4d77aa10cc02",
    "addresses": ["10.77.0.22", "10.88.0.32"],
    "image_digest": "sha256:aa03...41",
    "state_digest_before": "seed-0091bd",
}


async def open_run(client, **spec):
    spec.setdefault("tool", "zap")
    spec.setdefault("targets", ["shopfront", "edge"])
    spec.setdefault("addresses", {"shopfront": SHOPFRONT, "edge": EDGE})
    return (await client.post("/v1/runs", json=spec)).json()


async def test_addresses_are_recorded_at_open(client):
    run = await open_run(client)
    assert run["addresses"]["shopfront"] == {**SHOPFRONT, "state_digest_after": None}
    assert run["addresses"]["edge"]["addresses"] == ["10.77.0.22", "10.88.0.32"]


async def test_the_record_is_readable_on_its_own(client):
    run = await open_run(client)
    fetched = (await client.get(f"/v1/runs/{run['run_id']}")).json()
    assert fetched == run
    assert (await client.get("/v1/runs/nope")).status_code == 404


async def test_the_export_carries_the_record(client):
    """The scorer resolves an observed source address to an app through this map, so
    an export that does not carry it is not self-contained."""
    run = await open_run(client)
    await client.post("/v1/events", json={"events": [http_request_event()]})

    page = (await client.get(f"/v1/runs/{run['run_id']}/events")).json()
    assert page["run"]["run_id"] == run["run_id"]
    assert page["run"]["addresses"]["shopfront"]["addresses"] == ["10.77.0.21", "10.88.0.31"]

    # Every page, not just the first: a paginated export must stay self-contained.
    tail = (
        await client.get(f"/v1/runs/{run['run_id']}/events", params={"after_seq": 1})
    ).json()
    assert tail["run"]["addresses"] == page["run"]["addresses"]


async def test_a_dual_homed_targets_addresses_are_all_present(client):
    """The whole point of taking this from `docker inspect` rather than inferring it:
    a target reaches the collector on one network and makes its outbound requests on
    another, so the address the collector stamps on a correlation is never the address
    the sinkhole observes."""
    run = await open_run(client)
    addresses = run["addresses"]["shopfront"]["addresses"]
    assert {address.split(".")[1] for address in addresses} == {"77", "88"}


async def test_unknown_fields_from_docker_inspect_survive(client):
    run = await open_run(
        client,
        addresses={"shopfront": {**SHOPFRONT, "labels": {"com.docker.compose.project": "platform-edge"}}},
        targets=["shopfront"],
    )
    assert run["addresses"]["shopfront"]["labels"] == {"com.docker.compose.project": "platform-edge"}


async def test_close_merges_the_after_digest(client):
    run = await open_run(client)
    closed = (
        await client.post(
            f"/v1/runs/{run['run_id']}/close",
            json={"addresses": {"shopfront": {"state_digest_after": "seed-7f31a0"}}},
        )
    ).json()

    record = closed["addresses"]["shopfront"]
    assert record["state_digest_after"] == "seed-7f31a0"
    # A partial close must not erase what was captured at open.
    assert record["addresses"] == SHOPFRONT["addresses"]
    assert record["image_digest"] == SHOPFRONT["image_digest"]
    assert closed["addresses"]["edge"]["image_digest"] == EDGE["image_digest"]
    assert closed["active"] is False


async def test_a_target_that_did_not_reset_is_reported(client, caplog):
    """Enforcement is the orchestrator's (it holds the next run), but the discrepancy
    must be impossible to miss afterwards: a target left dirty contaminates whatever
    runs next, and both scores then partly describe the other run."""
    run = await open_run(client)
    with caplog.at_level(logging.WARNING, logger="bench.collector"):
        closed = (
            await client.post(
                f"/v1/runs/{run['run_id']}/close",
                json={"addresses": {"shopfront": {"state_digest_after": "drifted-1122"}}},
            )
        ).json()

    assert "did not return to its seeded state" in caplog.text
    # Kept, both of them, so a reader can check it themselves.
    assert closed["addresses"]["shopfront"]["state_digest_before"] == "seed-7f31a0"
    assert closed["addresses"]["shopfront"]["state_digest_after"] == "drifted-1122"


async def test_close_still_works_with_no_body_or_a_bad_one(client):
    """Never strand a run open over its metadata: the next run would have to force
    its way past it, and forcing is how two benchmarks end up sharing one stream."""
    first = await open_run(client)
    assert (await client.post(f"/v1/runs/{first['run_id']}/close")).status_code == 200

    second = await open_run(client)
    response = await client.post(
        f"/v1/runs/{second['run_id']}/close",
        content=b"{ not json",
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 200
    assert response.json()["active"] is False
    # The record captured at open is untouched by a rejected close body.
    assert response.json()["addresses"]["shopfront"]["image_digest"] == SHOPFRONT["image_digest"]


async def test_targets_and_addresses_disagreeing_is_reported_not_fatal(client, caplog):
    with caplog.at_level(logging.WARNING, logger="bench.collector"):
        run = await open_run(client, targets=["shopfront", "edge", "unmapped"])
    assert run["run_id"]
    assert "disagree" in caplog.text
    assert "unmapped" in caplog.text


async def test_the_record_survives_a_collector_restart(settings):
    async with client_for(settings) as (http, _):
        run = (
            await http.post(
                "/v1/runs",
                json={"tool": "zap", "targets": ["shopfront"], "addresses": {"shopfront": SHOPFRONT}},
            )
        ).json()

    async with client_for(settings) as (http, _):
        fetched = (await http.get(f"/v1/runs/{run['run_id']}")).json()
        assert fetched["addresses"]["shopfront"]["container_id"] == "9c1f0e2b7a44"


async def test_a_run_needs_no_addresses(client):
    """Local debugging and the unit tests open runs without a stack behind them."""
    run = (await client.post("/v1/runs", json={"tool": "zap"})).json()
    assert run["addresses"] == {}
