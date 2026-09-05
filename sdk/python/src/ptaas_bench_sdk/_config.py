"""Configuration, resolved from explicit arguments then the environment.

Targets are containers started by compose with BENCH_APP / BENCH_COLLECTOR_URL set,
so the environment is the primary source; explicit arguments exist for tests and for
apps that already own their config loading.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from typing import Any

DEFAULT_COLLECTOR_URL = "http://collector:8900"
DEFAULT_APP = "unknown"

# Batch and interval come straight from the contract: batches are capped at 500 events
# by the collector, and 250 ms is short enough that a run closed right after a scan
# still contains the last requests.
BATCH_MAX = 500
FLUSH_INTERVAL_S = 0.25

# 10k events ~= a few MB of queue. Past that the collector is clearly not draining and
# the newest events are the interesting ones, so the oldest are dropped (and counted).
QUEUE_MAX = 10_000

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    lowered = raw.strip().lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


@dataclass(frozen=True)
class BenchConfig:
    app: str = DEFAULT_APP
    collector_url: str = DEFAULT_COLLECTOR_URL
    enabled: bool = True
    queue_max: int = QUEUE_MAX
    batch_max: int = BATCH_MAX
    flush_interval: float = FLUSH_INTERVAL_S
    # Total budget for one POST to the collector. It runs on the background thread, so
    # this only bounds how long a wedged collector can stall the *flusher*, never the app.
    timeout: float = 5.0
    # Requests carrying this header (any value) are the platform's own traffic.
    selftest_header: str = "x-bench-selftest"
    seeder_user_agent: str = "ptaas-bench-seeder"
    # Body bytes buffered for enumeration. Beyond this the rest streams through
    # untouched and only the prefix is enumerated.
    max_body_bytes: int = 262_144
    max_params: int = 1024

    def with_overrides(self, **kwargs: Any) -> "BenchConfig":
        return replace(self, **{k: v for k, v in kwargs.items() if v is not None})


def config_from_env(**overrides: Any) -> BenchConfig:
    """Build a config from BENCH_* variables, with non-None overrides winning."""
    base = BenchConfig(
        app=os.environ.get("BENCH_APP", DEFAULT_APP),
        collector_url=os.environ.get("BENCH_COLLECTOR_URL", DEFAULT_COLLECTOR_URL),
        enabled=_env_bool("BENCH_ENABLED", True),
        queue_max=_env_int("BENCH_QUEUE_MAX", QUEUE_MAX),
        batch_max=min(_env_int("BENCH_BATCH_MAX", BATCH_MAX), BATCH_MAX),
        flush_interval=_env_float("BENCH_FLUSH_INTERVAL_MS", FLUSH_INTERVAL_S * 1000.0) / 1000.0,
        timeout=_env_float("BENCH_TIMEOUT_S", 5.0),
        selftest_header=os.environ.get("BENCH_SELFTEST_HEADER", "x-bench-selftest").lower(),
        seeder_user_agent=os.environ.get("BENCH_SEEDER_UA", "ptaas-bench-seeder"),
        max_body_bytes=_env_int("BENCH_MAX_BODY_BYTES", 262_144),
        max_params=_env_int("BENCH_MAX_PARAMS", 1024),
    )
    return base.with_overrides(**overrides)
