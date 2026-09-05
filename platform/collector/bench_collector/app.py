"""HTTP surface, implementing platform/collector/openapi.yaml.

Everything here is thin: routing, status codes and the one piece of policy the API
owns -- exactly one active run at a time. SDKs never send a run id, so attribution
cannot desynchronise when a target restarts mid-scan.

The service presents itself as an ordinary self-hosted OpenTelemetry collector.
That is not decoration: targets are dual-homed so they can report inward, so a tool
that wins RCE on a target can reach this service at the endpoint named in the
target's environment. Ingestion therefore answers on /v1/traces as well, and the
generated API description stays unpublished unless someone asks for it.
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse

from .config import load_settings
from .ingest import Collector, parse_address
from .schemas import EVENT_TYPES, CorrelationCreate, EventEnvelope, RunCreate

log = logging.getLogger("bench.collector")


# Reachable by anything that can talk to the port; everything else is the control
# surface and answers 404 to a client outside TELEMETRY_CONTROL_CIDRS.
INSTRUMENTATION_ROUTES = {
    ("GET", "/healthz"),
    ("POST", "/v1/events"),
    ("POST", "/v1/traces"),
    ("POST", "/v1/correlations"),
}


def _install_control_guard(app: FastAPI, collector: Collector) -> None:
    """Keep the answer key away from a target that has been popped.

    Targets are dual-homed so they can report inward, which means a tool holding RCE
    on one can reach this port at the address in the target's environment. The
    network split does not help there. So the control surface -- run management, the
    event export that lists exactly which planted sinks fired, the stats -- is
    additionally limited by source address.

    Empty allowlist means open, so an operator who has not configured this is exactly
    where they were before. Unauthorised callers get 404 rather than 403: a refusal
    confirms there is something to refuse.

    Enabling it means allowlisting the orchestrator AND the sinkhole, which reads the
    pending correlation set over the internal network.
    """
    networks = collector.settings.control_networks
    if not networks:
        log.warning(
            "control surface open to anything that can reach the port "
            "(set TELEMETRY_CONTROL_CIDRS to restrict it)"
        )
        return

    @app.middleware("http")
    async def guard(request: Request, call_next):
        route = (request.method.upper(), request.url.path)
        if route not in INSTRUMENTATION_ROUTES:
            client = request.client
            address = parse_address(client.host if client else None)
            if address is None or not any(address in network for network in networks):
                log.warning("refused control request %s %s from %s", *route, client)
                return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content={"detail": "Not Found"})
        return await call_next(request)


def _peer(request: Request) -> str | None:
    """The address on the other end of this connection. Never a header."""
    return request.client.host if request.client else None


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
    collector = collector or Collector(load_settings())
    settings = collector.settings
    expose = settings.expose_schema
    app = FastAPI(
        title=settings.service_name,
        summary="OpenTelemetry collector",
        version="1.0.0",
        lifespan=lifespan,
        # A served schema listing runs, events and stats would tell a tool holding
        # RCE on a target exactly what it is talking to, and where the answer key is.
        openapi_url="/openapi.json" if expose else None,
        docs_url="/docs" if expose else None,
        redoc_url="/redoc" if expose else None,
    )
    app.state.collector = collector
    _install_control_guard(app, collector)

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
        type: EVENT_TYPES | None = None,
        after_seq: int | None = Query(default=None, ge=0),
        limit: int = Query(default=5000, ge=1, le=50000),
    ) -> dict[str, Any]:
        if await collector.get_run(run_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown run")
        return await collector.get_events(run_id, event_type=type, after_seq=after_seq, limit=limit)

    @app.post(
        "/v1/traces",
        status_code=status.HTTP_202_ACCEPTED,
        summary="Ingest a batch of spans",
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": EventEnvelope.model_json_schema()}},
            }
        },
    )
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

        Answers on /v1/traces too: a target whose environment points at an OTLP-ish
        path is unremarkable, one pointing at something called a bench collector is
        an immediate tell to anyone who reads that environment.

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
                result = collector.submit(events, peer=_peer(request))
        except Exception:
            # Fire-and-forget contract: a target's request handler must not fail, and
            # must not slow down, because the collector had a bad day.
            collector.counters["dropped_invalid"] += 1
            log.exception("failed to ingest event batch")
            result = {"accepted": 0, "dropped": 1, "discarded_idle": 0}
        return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content=result)

    @app.post("/v1/correlations", status_code=status.HTTP_202_ACCEPTED)
    async def register_correlation(request: Request, collector: CollectorDep) -> Response:
        """Register an outbound-fetch hint for the egress sinkhole.

        Called by a planted sink immediately before it makes an attacker-controlled
        outbound request. Same fire-and-forget contract as ingestion -- a malformed
        hint is dropped and counted, never 4xx'd -- because this runs inside the
        target's request handling and several oracles are timing-based.

        The record is live in memory before this returns, since the callback it
        describes can arrive within milliseconds.
        """
        try:
            raw = await request.body()
            spec = CorrelationCreate.model_validate_json(raw)
        except Exception:
            collector.counters["dropped_invalid"] += 1
            log.exception("dropping malformed correlation")
            return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content={"registered": False})
        entry = collector.register_correlation(spec, peer=_peer(request))
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={"registered": True, "correlation": entry},
        )

    @app.get("/v1/correlations")
    async def list_correlations(
        collector: CollectorDep,
        destination_host: str | None = None,
        registered_after: float | None = None,
        include_expired: bool = False,
        wait_ms: int = Query(default=0, ge=0, le=5000),
    ) -> dict[str, Any]:
        """Pending hints, for the sinkhole to attribute a callback it just observed.

        Expired entries are evicted on read; `include_expired` is a debugging escape
        hatch and returns nothing extra once eviction has run.

        `wait_ms` long-polls for a matching hint. The hint and the callback race by
        design -- the SDK dispatches each hint on its own connection because the DNS
        lookup follows within microseconds -- so a sinkhole that observed the callback
        first can wait here instead of concluding it was unattributable. Bounded at
        5s, and never required: both sides are in the run's event stream, so the
        authoritative join is offline and order-independent.

        `registered_after` is an incremental cursor: the sinkhole polls this endpoint
        on a timer to keep its container-to-app map warm, which does not need the
        whole set every time.
        """
        entries = await collector.correlations.wait_for(
            destination_host, wait_ms / 1000, registered_after
        )
        return {
            "now": collector.correlations.clock(),
            "ttl": collector.correlations.ttl,
            "count": len(entries),
            "correlations": entries,
            "include_expired": include_expired,
        }

    @app.get("/v1/stats")
    async def stats(collector: CollectorDep) -> dict[str, Any]:
        """Plain-JSON debug counters. Deliberately not Prometheus: nothing scrapes
        the internal network, and a human reading `curl | jq` is the actual use case."""
        return await collector.stats()

    return app


app = create_app()
