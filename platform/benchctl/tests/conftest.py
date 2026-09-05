"""Shared fixtures: synthetic catalogs and synthetic event streams.

Tests never read the real ``catalog/vulns/`` (it changes as vulnerabilities are
planted, and a scoring test that breaks because someone added a vulnerability is a
useless test). They do reuse the real ``schema.json`` and ``taxonomy.yaml``, since
those are frozen contracts: validating synthetic entries against the real schema is
exactly what we want.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Any, Mapping, Sequence

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
REAL_SCHEMA = REPO_ROOT / "catalog" / "schema.json"
REAL_TAXONOMY = REPO_ROOT / "catalog" / "taxonomy.yaml"


def sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def vuln_entry(**overrides: Any) -> dict[str, Any]:
    """A schema-valid vulnerability entry, overridable field by field."""
    entry: dict[str, Any] = {
        "id": "BENCH-SHOP-0001",
        "title": "Synthetic SQL injection for tests",
        "app": "shopfront",
        "component": "api",
        "class": "sqli_union",
        "severity": "critical",
        "entrypoint": {
            "method": "GET",
            "path": "/api/products",
            "auth": "none",
            "param": "q",
            "param_in": "query",
            "default_value": "laptop",
        },
        "discovery": {"render": "static-html", "requires": ["form-submit"], "difficulty": 1},
        "oracle": {
            "kind": "sink",
            "condition": "The database parsed an injected clause supplied by the caller.",
        },
    }
    for key, value in overrides.items():
        if isinstance(value, Mapping) and isinstance(entry.get(key), dict):
            entry[key] = {**entry[key], **value}
        else:
            entry[key] = value
    return entry


@pytest.fixture
def make_catalog(tmp_path: Path):
    """Materialise a catalog root from a list of entry dicts."""

    def _make(entries: Sequence[Mapping[str, Any]], *, taxonomy: Mapping[str, Any] | None = None) -> Path:
        root = tmp_path / f"repo{len(list(tmp_path.iterdir()))}"
        (root / "catalog" / "vulns").mkdir(parents=True)
        shutil.copy(REAL_SCHEMA, root / "catalog" / "schema.json")
        if taxonomy is None:
            shutil.copy(REAL_TAXONOMY, root / "catalog" / "taxonomy.yaml")
        else:
            (root / "catalog" / "taxonomy.yaml").write_text(yaml.safe_dump(taxonomy), encoding="utf-8")
        for i, entry in enumerate(entries):
            name = entry.get("id", f"entry-{i}") if isinstance(entry, Mapping) else f"entry-{i}"
            (root / "catalog" / "vulns" / f"{name}.yaml").write_text(
                yaml.safe_dump(dict(entry), sort_keys=False), encoding="utf-8"
            )
        return root

    return _make


def http_event(**overrides: Any) -> dict[str, Any]:
    event: dict[str, Any] = {
        "type": "http_request",
        "app": "shopfront",
        "ts": 1000.0,
        "method": "GET",
        "route": "/api/products",
        "path": "/api/products",
        "status": 200,
        "params": [],
    }
    event.update(overrides)
    return event


def param(name: str, value: str | None, location: str = "query") -> dict[str, Any]:
    """Build a params[] entry the way an SDK would, hashing the value."""
    if value is None:
        return {"name": name, "in": location}
    return {
        "name": name,
        "in": location,
        "value_sha256": sha(value),
        "value_len": len(value),
        "sample": value[:256],
    }


def trigger_event(vuln_id: str, **overrides: Any) -> dict[str, Any]:
    event: dict[str, Any] = {
        "type": "trigger",
        "app": "shopfront",
        "ts": 1001.0,
        "vuln_id": vuln_id,
        "oracle_kind": "sink",
        "evidence": {"payload": "' UNION SELECT 1--", "detail": "second table in result set"},
    }
    event.update(overrides)
    return event


def oob_event(token: str, **overrides: Any) -> dict[str, Any]:
    event: dict[str, Any] = {
        "type": "oob",
        "app": "canary",
        "ts": 1002.0,
        "token": token,
        "channel": "dns",
        "source_ip": "10.0.0.9",
    }
    event.update(overrides)
    return event
