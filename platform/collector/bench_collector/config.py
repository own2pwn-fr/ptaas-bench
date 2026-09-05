"""Runtime configuration, read from the environment at app-creation time.

Read lazily (not at import) so tests can point a fresh app at a fresh SQLite file
without process-wide state leaking between test cases.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _normalise_database_url(url: str) -> str:
    """Force an asyncio-capable driver on a plain DSN.

    docker-compose.yml hands us ``postgresql://bench:bench@collector-db:5432/bench``
    because that is the DSN every other tool understands; SQLAlchemy's async engine
    needs the driver spelled out. Rewriting here means the deployment config stays
    driver-agnostic.
    """
    if url.startswith(("postgresql+", "sqlite+")):
        return url
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url[len("postgresql://") :]
    if url.startswith("postgres://"):
        return "postgresql+asyncpg://" + url[len("postgres://") :]
    if url.startswith("sqlite://"):
        return "sqlite+aiosqlite://" + url[len("sqlite://") :]
    return url


@dataclass(frozen=True)
class Settings:
    database_url: str
    queue_maxsize: int
    write_batch: int
    sql_echo: bool


def load_settings() -> Settings:
    return Settings(
        database_url=_normalise_database_url(
            os.environ.get("BENCH_DATABASE_URL", "sqlite+aiosqlite:///./bench-collector.db")
        ),
        # A bounded queue is a safety valve, not a throttle: a target under a heavy
        # scan must never block on us, so an overflow drops events (and is counted)
        # instead of applying backpressure to the instrumented application.
        queue_maxsize=int(os.environ.get("BENCH_QUEUE_MAXSIZE", "200000")),
        write_batch=int(os.environ.get("BENCH_WRITE_BATCH", "500")),
        sql_echo=os.environ.get("BENCH_SQL_ECHO", "").lower() in {"1", "true", "yes"},
    )
