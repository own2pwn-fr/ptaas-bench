"""Run lifecycle: exactly one active run, and force is the only way past it."""

from __future__ import annotations


async def test_healthz(client):
    response = await client.get("/healthz")
    assert response.status_code == 200


async def test_run_lifecycle(client):
    created = await client.post(
        "/v1/runs",
        json={
            "tool": "zap",
            "tool_version": "2.15.0",
            "profile": "full",
            "targets": ["shopfront"],
            "notes": "baseline",
        },
    )
    assert created.status_code == 201
    run = created.json()
    assert run["tool"] == "zap"
    assert run["tool_version"] == "2.15.0"
    assert run["profile"] == "full"
    assert run["targets"] == ["shopfront"]
    assert run["active"] is True
    assert run["closed_at"] is None
    assert run["event_count"] == 0
    assert run["run_id"]

    active = await client.get("/v1/runs/active")
    assert active.status_code == 200
    assert active.json()["run_id"] == run["run_id"]

    closed = await client.post(f"/v1/runs/{run['run_id']}/close")
    assert closed.status_code == 200
    assert closed.json()["active"] is False
    assert closed.json()["closed_at"] is not None

    assert (await client.get("/v1/runs/active")).status_code == 404


async def test_second_run_conflicts_without_force(client):
    first = (await client.post("/v1/runs", json={"tool": "zap"})).json()

    conflict = await client.post("/v1/runs", json={"tool": "burp"})
    assert conflict.status_code == 409

    still_active = await client.get("/v1/runs/active")
    assert still_active.json()["run_id"] == first["run_id"]


async def test_force_closes_the_previous_run(client):
    first = (await client.post("/v1/runs", json={"tool": "zap"})).json()

    forced = await client.post("/v1/runs", json={"tool": "burp", "force": True})
    assert forced.status_code == 201
    second = forced.json()
    assert second["run_id"] != first["run_id"]

    active = await client.get("/v1/runs/active")
    assert active.json()["run_id"] == second["run_id"]

    runs = {run["run_id"]: run for run in (await client.get("/v1/runs")).json()}
    assert runs[first["run_id"]]["active"] is False
    assert runs[first["run_id"]]["closed_at"] is not None
    assert runs[second["run_id"]]["active"] is True


async def test_run_after_close_needs_no_force(client):
    first = (await client.post("/v1/runs", json={"tool": "zap"})).json()
    await client.post(f"/v1/runs/{first['run_id']}/close")

    second = await client.post("/v1/runs", json={"tool": "burp"})
    assert second.status_code == 201


async def test_closing_twice_is_idempotent(client):
    run = (await client.post("/v1/runs", json={"tool": "zap"})).json()
    first = await client.post(f"/v1/runs/{run['run_id']}/close")
    second = await client.post(f"/v1/runs/{run['run_id']}/close")
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["closed_at"] == first.json()["closed_at"]


async def test_unknown_run_is_404(client):
    assert (await client.post("/v1/runs/does-not-exist/close")).status_code == 404
    assert (await client.get("/v1/runs/does-not-exist/events")).status_code == 404


async def test_run_requires_a_tool(client):
    assert (await client.post("/v1/runs", json={})).status_code == 422
