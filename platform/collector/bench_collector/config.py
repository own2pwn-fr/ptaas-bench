"""Runtime configuration, read from the environment at app-creation time.

Read lazily (not at import) so tests can point a fresh app at a fresh SQLite file
without process-wide state leaking between test cases.

Every setting is looked up under a ``TELEMETRY_`` name first and a legacy ``BENCH_``
name second. The deployed stack presents this service as an ordinary self-hosted
OpenTelemetry collector; a target process whose environment names a "bench" anything
tells a tool that compromised it exactly what it is looking at.
"""

from __future__ import annotations

import ipaddress
import logging
import os
from dataclasses import dataclass

log = logging.getLogger("bench.collector")

DEFAULT_CORRELATION_TTL = 120.0


def _env(*names: str, default: str = "") -> str:
    for name in names:
        value = os.environ.get(name)
        if value not in (None, ""):
            return value
    return default


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


def parse_cidrs(raw: str) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    """Parse a comma/space separated CIDR list, skipping (loudly) what will not parse.

    A typo here silently stops the platform's own seeding traffic from being marked
    synthetic, which would credit every tool with our requests. Better to log the bad
    entry and keep the good ones than to refuse to start and lose a whole run.
    """
    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for chunk in raw.replace(",", " ").split():
        try:
            networks.append(ipaddress.ip_network(chunk, strict=False))
        except ValueError:
            log.error("ignoring unparsable synthetic CIDR %r", chunk)
    return tuple(networks)


@dataclass(frozen=True)
class Settings:
    database_url: str
    queue_maxsize: int
    write_batch: int
    sql_echo: bool
    # Source addresses whose traffic belongs to the platform (seeding, self-test,
    # health checks). Events from these are never scored.
    synthetic_networks: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = ()
    # Addresses allowed to reach the control surface (run management, event export,
    # stats). Empty means open, which is what the network split alone gives you.
    control_networks: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = ()
    correlation_ttl: float = DEFAULT_CORRELATION_TTL
    correlation_max: int = 20_000
    # Off by default: targets are dual-homed, so a tool holding RCE on one can reach
    # this service. A served OpenAPI document describing runs and events would be a
    # complete confession.
    expose_schema: bool = False
    service_name: str = "otel-collector"


def load_settings() -> Settings:
    return Settings(
        database_url=_normalise_database_url(
            _env(
                "TELEMETRY_DATABASE_URL",
                "BENCH_DATABASE_URL",
                default="sqlite+aiosqlite:///./telemetry.db",
            )
        ),
        # A bounded queue is a safety valve, not a throttle: a target under a heavy
        # scan must never block on us, so an overflow drops events (and is counted)
        # instead of applying backpressure to the instrumented application.
        queue_maxsize=int(_env("TELEMETRY_QUEUE_MAXSIZE", "BENCH_QUEUE_MAXSIZE", default="200000")),
        write_batch=int(_env("TELEMETRY_WRITE_BATCH", "BENCH_WRITE_BATCH", default="500")),
        sql_echo=_env("TELEMETRY_SQL_ECHO", "BENCH_SQL_ECHO").lower() in {"1", "true", "yes"},
        synthetic_networks=parse_cidrs(_env("TELEMETRY_SYNTHETIC_CIDRS", "BENCH_SYNTHETIC_CIDRS")),
        control_networks=parse_cidrs(_env("TELEMETRY_CONTROL_CIDRS", "BENCH_CONTROL_CIDRS")),
        correlation_ttl=float(
            _env(
                "TELEMETRY_CORRELATION_TTL",
                "BENCH_CORRELATION_TTL",
                default=str(DEFAULT_CORRELATION_TTL),
            )
        ),
        correlation_max=int(_env("TELEMETRY_CORRELATION_MAX", default="20000")),
        expose_schema=_env("TELEMETRY_EXPOSE_SCHEMA").lower() in {"1", "true", "yes"},
        service_name=_env("TELEMETRY_SERVICE_NAME", default="otel-collector"),
    )
