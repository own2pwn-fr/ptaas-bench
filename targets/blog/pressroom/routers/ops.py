"""Service status, the front end's configuration, and the diagnostics view.

The first two are public because the front end and our uptime checks both read them.
The third is the estate's standard diagnostics blueprint, which every service carries
and which the network policy is supposed to fence off.
"""

from __future__ import annotations

import dataclasses
import os
import platform
import time
from typing import Any

from fastapi import APIRouter, Request

from .. import __version__
from ..observability import telemetry
from ..settings import settings
from ..store import cache, database

router = APIRouter(prefix="/api", tags=["platform"])

STARTED = time.time()
REDACTED = "***"
SECRET_SETTINGS = ("session_secret", "preview_signing_key")


def _peer(request: Request) -> str:
    client = request.scope.get("client") or ()
    return client[0] if client else ""


@router.get("/status", summary="Service status")
def status() -> dict[str, Any]:
    return {
        "service": "pressroom",
        "version": __version__,
        "state": "ok",
        "uptime_s": int(time.time() - STARTED),
    }


@router.get("/config", summary="Configuration the front end needs")
def config() -> dict[str, Any]:
    cfg = settings()
    return {
        "publication": cfg.site_name,
        "domain": cfg.site_domain,
        "analytics_site": cfg.analytics_site_id,
        "embed_providers": list(cfg.embed_providers),
        "comment_length": 4000,
        "search_length": 1500,
        "cadences": ["daily", "weekly", "monthly", "never"],
    }


@router.get("/internal/diagnostics", summary="Runtime diagnostics")
def diagnostics(request: Request) -> dict[str, Any]:
    """Everything an on-call engineer wants before they open a shell.

    Settings, where the datastores are, whether they answer, and how the process was
    started. Passphrases are printed redacted -- what is wanted here is whether a value
    is set, not what it is.
    """
    cfg = settings()
    effective = {}
    for field in dataclasses.fields(cfg):
        if field.name.startswith("_"):
            continue
        value = getattr(cfg, field.name)
        effective[field.name] = REDACTED if field.name in SECRET_SETTINGS else value

    stores = {}
    for name, probe in (("documents", _probe_documents), ("cache", _probe_cache)):
        started = time.monotonic()
        try:
            probe()
            stores[name] = {"state": "ok",
                            "took_ms": round((time.monotonic() - started) * 1000, 2)}
        except Exception as error:  # noqa: BLE001
            stores[name] = {"state": "unreachable", "detail": str(error)[:200]}

    payload = {
        "service": "pressroom",
        "version": __version__,
        "started_at": int(STARTED),
        "settings": effective,
        "datastores": stores,
        "runtime": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "pid": os.getpid(),
            "cwd": os.getcwd(),
            "user": os.environ.get("USER", ""),
        },
        "assist_endpoint": os.environ.get("ASSIST_MODEL_ENDPOINT", ""),
    }
    if not cfg.from_ops_range(_peer(request)):
        telemetry.signal("blog.ops.diagnostics.external_read", {
            "payload": request.url.path,
            "detail": (f"served the diagnostics view to {_peer(request)}, which is not "
                       f"in the operations range ({', '.join(cfg.ops_cidrs)})"),
        })
    return payload


def _probe_documents() -> None:
    database().command("ping")


def _probe_cache() -> None:
    cache().ping()
