"""Test fixtures: a real app instance, real lifespan, SQLite on disk.

The lifespan is entered explicitly because httpx's ASGI transport does not run
startup/shutdown -- and startup is where the schema and the writer task come from,
so skipping it would test a different program than the one that ships.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from bench_collector.app import create_app
from bench_collector.config import Settings, parse_cidrs
from bench_collector.ingest import Collector

# The platform's own network in these tests. Traffic from here is the seeding and
# self-test traffic that must never be credited to a tool.
PLATFORM_CIDRS = "10.99.0.0/16, fd00:99::/32"
TOOL_IP = "192.0.2.77"
PLATFORM_IP = "10.99.4.12"


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'telemetry.db'}",
        queue_maxsize=10_000,
        write_batch=200,
        sql_echo=False,
        synthetic_networks=parse_cidrs(PLATFORM_CIDRS),
        correlation_ttl=120.0,
        correlation_max=20_000,
        # Off in production, on here: the tests assert the documented contract, and
        # one of them asserts that it is off by default.
        expose_schema=True,
    )


@asynccontextmanager
async def client_for(settings: Settings | None = None, collector: Collector | None = None):
    """A running app on the given settings, yielded as (client, collector)."""
    collector = collector or Collector(settings)
    app = create_app(collector)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://otel-collector:8900") as http,
    ):
        yield http, collector


@pytest.fixture
def collector(settings: Settings) -> Collector:
    return Collector(settings)


@pytest_asyncio.fixture
async def client(collector: Collector):
    async with client_for(collector=collector) as (http, _collector):
        yield http


def http_request_event(**overrides) -> dict:
    event = {
        "type": "http_request",
        "app": "shopfront",
        "ts": 1735689600.5,
        "method": "GET",
        "route": "/api/orders/:id",
        "path": "/api/orders/42",
        "status": 200,
        "params": [{"name": "id", "in": "path", "value_len": 2, "sample": "42"}],
    }
    event.update(overrides)
    return event


def trigger_event(**overrides) -> dict:
    event = {
        "type": "trigger",
        "app": "shopfront",
        "signal": "shop.catalog.query.plan_anomaly",
        "oracle_kind": "sink",
        "evidence": {"payload": "' OR 1=1--", "detail": "tautology reached the query planner"},
    }
    event.update(overrides)
    return event


def correlation(**overrides) -> dict:
    record = {
        "app": "shopfront",
        "signal": "shop.import.feed.remote_fetch",
        "destination_host": "9f2c.oast.fun",
        "route": "/api/import/feed",
        "param": "url",
        "request_id": "req-7781",
    }
    record.update(overrides)
    return record
