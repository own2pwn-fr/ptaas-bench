"""HTTP surface, implementing platform/collector/openapi.yaml.

Everything here is thin: routing, status codes and the one piece of policy the API
owns -- exactly one active run at a time. SDKs never send a run id, so attribution
cannot desynchronise when a target restarts mid-scan.
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse

from .config import load_settings
from .ingest import Collector
from .schemas import EventEnvelope, RunCreate

log = logging.getLogger("bench.collector")


def get_collector(request: Request) -> Collector:
    return request.app.state.collector


CollectorDep = Annotated[Collector, Depends(get_collector)]


@asynccontextmanager
async def lifespan(app: FastAPI):
    collector: Collector = app.state.collector
    await collector.start()
    try:
        yield
    finally:
        await collector.stop()


def create_app(collector: Collector | None = None) -> FastAPI:
    app = FastAPI(
        title="ptaas-bench collector",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.state.collector = collector or Collector(load_settings())

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {"status": "ok"}

    @app.post("/v1/runs", status_code=status.HTTP_201_CREATED)
    async def create_run(spec: RunCreate, collector: CollectorDep) -> JSONResponse:
        run, ok = await collector.open_run(spec, force=spec.force)
        if not ok:
            # 409 rather than silently stealing the run: two benchmarks writing into
            # one event stream would make both results unusable, and the operator
            # must say explicitly (force=true) that the previous one is abandoned.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="a run is already active; pass force=true to close it first",
            )
        assert run is not None
        return JSONResponse(status_code=status.HTTP_201_CREATED, content=run.as_dict())

    @app.get("/v1/runs")
    async def list_runs(collector: CollectorDep) -> list[dict[str, Any]]:
        return [run.as_dict() for run in await collector.list_runs()]

    @app.get("/v1/runs/active")
    async def active_run(collector: CollectorDep) -> dict[str, Any]:
        run_id = collector.active_run_id
        run = await collector.get_run(run_id) if run_id else None
        if run is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no active run")
        return run.as_dict()

    @app.post("/v1/runs/{run_id}/close")
    async def close_run(run_id: str, collector: CollectorDep) -> dict[str, Any]:
        run = await collector.close_run(run_id)
        if run is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown run")
        return run.as_dict()

    @app.get("/v1/runs/{run_id}/events")
    async def export_events(
        run_id: str,
        collector: CollectorDep,
        type: Literal["http_request", "trigger", "oob", "note"] | None = None,
        after_seq: int | None = Query(default=None, ge=0),
        limit: int = Query(default=5000, ge=1, le=50000),
    ) -> dict[str, Any]:
        if await collector.get_run(run_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown run")
        return await collector.get_events(run_id, event_type=type, after_seq=after_seq, limit=limit)

    @app.post(
        "/v1/events",
        status_code=status.HTTP_202_ACCEPTED,
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": EventEnvelope.model_json_schema()}},
            }
        },
    )
    async def ingest_events(request: Request, collector: CollectorDep) -> Response:
        """Always 202. Never validate the batch as a whole.

        The body is parsed by hand instead of being bound to a Pydantic model because
        FastAPI would answer 422 for the *whole* batch on a single bad item: one
        buggy SDK field would then delete a run's worth of ground truth. Bad items
        are dropped and counted (see /v1/stats), good ones are kept.
        """
        result = {"accepted": 0, "dropped": 0, "discarded_idle": 0}
        try:
            raw = await request.body()
            body = json.loads(raw) if raw else {}
            events = body.get("events") if isinstance(body, dict) else None
            if not isinstance(events, list):
                collector.counters["dropped_invalid"] += 1
                log.error("malformed /v1/events body (expected {'events': [...]}) : %s", raw[:512])
                result["dropped"] = 1
            else:
                result = collector.submit(events)
        except Exception:
            # Fire-and-forget contract: a target's request handler must not fail, and
            # must not slow down, because the collector had a bad day.
            collector.counters["dropped_invalid"] += 1
            log.exception("failed to ingest event batch")
            result = {"accepted": 0, "dropped": 1, "discarded_idle": 0}
        return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content=result)

    @app.get("/v1/stats")
    async def stats(collector: CollectorDep) -> dict[str, Any]:
        """Plain-JSON debug counters. Deliberately not Prometheus: nothing scrapes
        the internal network, and a human reading `curl | jq` is the actual use case."""
        return await collector.stats()

    return app


app = create_app()
