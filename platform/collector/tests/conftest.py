"""Test fixtures: a real app instance, real lifespan, SQLite on disk.

The lifespan is entered explicitly because httpx's ASGI transport does not run
startup/shutdown -- and startup is where the schema and the writer task come from,
so skipping it would test a different program than the one that ships.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from bench_collector.app import create_app
from bench_collector.config import Settings
from bench_collector.ingest import Collector


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'bench.db'}",
        queue_maxsize=10_000,
        write_batch=200,
        sql_echo=False,
    )


@pytest.fixture
def collector(settings: Settings) -> Collector:
    return Collector(settings)


@pytest_asyncio.fixture
async def client(collector: Collector):
    app = create_app(collector)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://collector:8900") as http:
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
        "vuln_id": "BENCH-SHOP-0001",
        "oracle_kind": "sink",
        "evidence": {"payload": "' OR 1=1--", "detail": "tautology reached the SQL sink"},
    }
    event.update(overrides)
    return event
