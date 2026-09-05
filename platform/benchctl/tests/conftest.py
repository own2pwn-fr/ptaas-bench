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
REAL_ROADMAP = REPO_ROOT / "catalog" / "roadmap.yaml"


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
    # Every non-oob oracle must declare an opaque signal (targets never emit ids),
    # and signals must be unique across the corpus, so derive one from the id.
    # `oracle: {"signal": None}` opts out, for the tests that need that case.
    signal = entry["oracle"].get("signal", "__derive__")
    if signal == "__derive__":
        # Derived from the whole id, not just its number: two apps share numbering
        # (BENCH-SHOP-0001 and BENCH-ADMN-0001 both exist) and signals are unique
        # corpus-wide.
        entry["oracle"]["signal"] = "shop.synthetic.{}.anomaly".format(
            entry["id"].replace("BENCH-", "").replace("-", "_").lower()
        )
    elif signal is None:
        entry["oracle"].pop("signal")
    return entry


@pytest.fixture
def make_catalog(tmp_path: Path):
    """Materialise a catalog root from a list of entry dicts."""

    def _make(
        entries: Sequence[Mapping[str, Any]],
        *,
        taxonomy: Mapping[str, Any] | None = None,
        roadmap: Mapping[str, Any] | None = None,
    ) -> Path:
        root = tmp_path / f"repo{len(list(tmp_path.iterdir()))}"
        (root / "catalog" / "vulns").mkdir(parents=True)
        shutil.copy(REAL_SCHEMA, root / "catalog" / "schema.json")
        if taxonomy is None:
            shutil.copy(REAL_TAXONOMY, root / "catalog" / "taxonomy.yaml")
        else:
            (root / "catalog" / "taxonomy.yaml").write_text(yaml.safe_dump(taxonomy), encoding="utf-8")
        if roadmap is None:
            # The real roadmap is the authority on id prefixes, so synthetic
            # catalogs are checked against the same one the corpus uses.
            shutil.copy(REAL_ROADMAP, root / "catalog" / "roadmap.yaml")
        elif roadmap:
            (root / "catalog" / "roadmap.yaml").write_text(
                yaml.safe_dump(dict(roadmap)), encoding="utf-8")
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


def signal_of(vuln_id: str) -> str:
    """The signal `vuln_entry` derives for an id, mirroring the target side."""
    return "shop.synthetic.{}.anomaly".format(
        vuln_id.replace("BENCH-", "").replace("-", "_").lower())


def trigger_event(for_vuln: str | None = None, **overrides: Any) -> dict[str, Any]:
    """A sink firing. Targets emit the opaque signal, never the catalog id."""
    event: dict[str, Any] = {
        "type": "trigger",
        "app": "shopfront",
        "ts": 1001.0,
        "oracle_kind": "sink",
        "evidence": {"payload": "' UNION SELECT 1--", "detail": "second table in result set"},
    }
    if for_vuln is not None:
        event["signal"] = signal_of(for_vuln)
    event.update(overrides)
    return event


def oob_event(token: str | None = None, **overrides: Any) -> dict[str, Any]:
    event: dict[str, Any] = {
        "type": "oob",
        "app": "canary",
        "ts": 1002.0,
        "channel": "dns",
        "source_ip": "10.0.0.9",
    }
    if token is not None:
        event["token"] = token
    event.update(overrides)
    return event


def routes_inventory(root, app: str, routes: Sequence[Mapping[str, Any]]) -> None:
    """Write targets/<app>/routes.yaml under a synthetic repo root."""
    directory = root / "targets" / app
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "routes.yaml").write_text(
        yaml.safe_dump({"app": app, "routes": [dict(r) for r in routes]}, sort_keys=False),
        encoding="utf-8",
    )
