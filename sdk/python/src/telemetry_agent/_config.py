"""Runtime configuration, read from explicit arguments then from the environment.

Services are deployed as containers whose environment carries the TELEMETRY_* keys, so
the environment is the primary source; explicit arguments exist for unit tests and for
services that already own their configuration loading.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from typing import Any

DEFAULT_ENDPOINT = "http://otel-collector:8900"
DEFAULT_SERVICE = "unknown-service"

# OTLP-style ingest paths on the collector.
EVENTS_PATH = "/v1/traces"
CORRELATIONS_PATH = "/v1/correlations"

# The collector rejects oversized payloads, so batches are capped well below it. 250 ms
# keeps the tail of a burst on the wire before a process is asked to shut down.
BATCH_MAX = 500
FLUSH_INTERVAL_S = 0.25

# ~10k records of headroom. Past that the collector is plainly not draining, and the
# most recent records are the ones worth keeping, so the oldest are discarded.
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


def _env_tuple(name: str) -> tuple[str, ...]:
    raw = os.environ.get(name, "")
    return tuple(item.strip() for item in raw.split(",") if item.strip())


@dataclass(frozen=True)
class TelemetryConfig:
    service: str = DEFAULT_SERVICE
    endpoint: str = DEFAULT_ENDPOINT
    enabled: bool = True
    queue_max: int = QUEUE_MAX
    batch_max: int = BATCH_MAX
    flush_interval: float = FLUSH_INTERVAL_S
    # Budget for one export call. It runs on the exporter thread, so it only bounds how
    # long an unresponsive collector can stall *that* thread, never a served request.
    timeout: float = 5.0
    # Networks whose traffic is generated rather than organic: uptime probes, warm-up
    # jobs, load generators. Records from those peers are marked synthetic so dashboards
    # and error budgets are computed on customer traffic only. Membership is decided on
    # the socket peer address and on nothing else -- see TelemetryClient.is_synthetic_peer.
    synthetic_cidrs: tuple[str, ...] = ()
    # Request-body bytes buffered for attribute extraction. Beyond this the remainder
    # streams straight through and only the prefix is described.
    max_body_bytes: int = 262_144
    max_params: int = 1024
    events_path: str = EVENTS_PATH
    correlations_path: str = CORRELATIONS_PATH

    def with_overrides(self, **kwargs: Any) -> "TelemetryConfig":
        return replace(self, **{k: v for k, v in kwargs.items() if v is not None})


def config_from_env(**overrides: Any) -> TelemetryConfig:
    """Build a configuration from TELEMETRY_* variables; non-None overrides win."""
    base = TelemetryConfig(
        service=os.environ.get("TELEMETRY_SERVICE", DEFAULT_SERVICE),
        endpoint=os.environ.get("TELEMETRY_ENDPOINT", DEFAULT_ENDPOINT),
        enabled=_env_bool("TELEMETRY_ENABLED", True),
        queue_max=_env_int("TELEMETRY_QUEUE_MAX", QUEUE_MAX),
        batch_max=min(_env_int("TELEMETRY_BATCH_MAX", BATCH_MAX), BATCH_MAX),
        flush_interval=_env_float("TELEMETRY_FLUSH_INTERVAL_MS", FLUSH_INTERVAL_S * 1000.0) / 1000.0,
        timeout=_env_float("TELEMETRY_TIMEOUT_S", 5.0),
        synthetic_cidrs=_env_tuple("TELEMETRY_SYNTHETIC_CIDRS"),
        max_body_bytes=_env_int("TELEMETRY_MAX_BODY_BYTES", 262_144),
        max_params=_env_int("TELEMETRY_MAX_PARAMS", 1024),
        events_path=os.environ.get("TELEMETRY_EVENTS_PATH", EVENTS_PATH),
        correlations_path=os.environ.get("TELEMETRY_CORRELATIONS_PATH", CORRELATIONS_PATH),
    )
    return base.with_overrides(**overrides)
