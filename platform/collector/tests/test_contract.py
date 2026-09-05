"""Keeps openapi.yaml honest.

The file went stale once already, and a contract that describes a service nobody
built is worse than no contract: the SDK author, the sinkhole author and the scorer
all read it instead of this code. These assertions are cheap and catch the drift that
actually happens — a route added without documenting it, an enum that grew on one
side only, a field renamed for deception reasons in the code but not in the spec.
"""

from __future__ import annotations

import typing
from pathlib import Path

import pytest
import yaml

from bench_collector.app import create_app
from bench_collector.config import Settings
from bench_collector.ingest import Collector
from bench_collector.schemas import EVENT_TYPES, CorrelationCreate, SignalEvent

SPEC_FILE = Path(__file__).resolve().parents[1] / "openapi.yaml"
# Forbidden strings from targets/target-contract.yaml that used to be part of the
# wire shape. They are gone from the SDK; they must not creep back in as field names.
RETIRED_FIELDS = ("oracle_kind", "X-Bench-Selftest")


@pytest.fixture(scope="module")
def spec() -> dict:
    return yaml.safe_load(SPEC_FILE.read_text())


@pytest.fixture(scope="module")
def implemented() -> set[tuple[str, str]]:
    app = create_app(Collector(Settings("sqlite+aiosqlite://", 10, 10, False)))
    routes = set()
    for route in app.routes:
        for method in getattr(route, "methods", set()) or set():
            if method in {"GET", "POST"}:
                routes.add((method, route.path))
    return routes


def documented(spec: dict) -> set[tuple[str, str]]:
    return {
        (method.upper(), path)
        for path, operations in spec["paths"].items()
        for method in operations
        if method in {"get", "post"}
    }


def test_every_documented_operation_exists(spec, implemented):
    assert documented(spec) - implemented == set()


def test_every_implemented_operation_is_documented(spec, implemented):
    undocumented = implemented - documented(spec)
    assert undocumented == set(), f"implemented but undocumented: {sorted(undocumented)}"


def test_export_filter_enum_matches_the_event_types(spec):
    parameters = spec["paths"]["/v1/runs/{run_id}/events"]["get"]["parameters"]
    enum = next(p for p in parameters if p["name"] == "type")["schema"]["enum"]
    assert set(enum) == set(typing.get_args(EVENT_TYPES))


def test_signal_event_documents_both_spellings(spec):
    schema = spec["components"]["schemas"]["SignalEvent"]["allOf"][1]
    assert set(schema["properties"]["type"]["enum"]) == set(typing.get_args(SignalEvent.model_fields["type"].annotation))
    assert "attributes" in schema["properties"]
    assert "evidence" in schema["properties"]


def test_correlation_body_matches_the_model(spec):
    documented_required = set(spec["components"]["schemas"]["CorrelationCreate"]["required"])
    model_required = {name for name, f in CorrelationCreate.model_fields.items() if f.is_required()}
    assert documented_required == model_required


def test_retired_wire_names_are_not_declared_fields(spec):
    """They may still arrive from a target not yet re-cut, and are preserved as
    unrecognised extras -- but the contract must not invite them."""
    for schema in spec["components"]["schemas"].values():
        for block in [schema, *schema.get("allOf", [])]:
            assert not set(block.get("properties") or {}) & set(RETIRED_FIELDS)


def test_the_spec_is_not_shipped_in_the_image():
    """It is repository documentation. Inside the container it would be a map of the
    grader for anything that wins RCE on a target and reaches this port."""
    ignored = (SPEC_FILE.parent / ".dockerignore").read_text().split()
    assert SPEC_FILE.name in ignored


def test_signal_pattern_matches_the_catalog_schema():
    """The catalog is the authority for what a signal name may look like. Both SDKs,
    this service and the catalog have to agree exactly: a name that one end accepts
    and another silently drops costs a finding with no error anywhere, and the loss
    looks like a tool that simply did not exploit the flaw."""
    import json

    from bench_collector.schemas import SIGNAL_PATTERN

    catalog = json.loads((SPEC_FILE.parents[2] / "catalog" / "schema.json").read_text())
    assert catalog["properties"]["oracle"]["properties"]["signal"]["pattern"] == SIGNAL_PATTERN


def test_documented_signal_patterns_match_the_implementation():
    from bench_collector.schemas import SIGNAL_PATTERN

    spec = yaml.safe_load(SPEC_FILE.read_text())
    schemas = spec["components"]["schemas"]
    documented = {
        schemas["SignalEvent"]["allOf"][1]["properties"]["signal"]["pattern"],
        schemas["CorrelationCreate"]["properties"]["signal"]["pattern"],
    }
    assert documented == {SIGNAL_PATTERN}
