"""Route inventories: loading, catalog cross-checks, crawl coverage, FP denominator."""

from __future__ import annotations

from benchctl.catalog import load_catalog
from benchctl.events import events_from_iterable
from benchctl.findings import classify_findings, finding_from_dict
from benchctl.inventory import (
    coverage_summary,
    crosscheck_inventory,
    load_inventories,
)
from benchctl.scoring import score_run
from conftest import http_event, param, routes_inventory, vuln_entry

SURFACE = [
    # The planted endpoint, plus the ordinary surface the deception mandate requires.
    {"path": "/api/products", "method": "GET", "auth": "none", "render": "static-html",
     "params": ["q"], "status": "planted"},
    {"path": "/api/catalog/items", "method": "GET", "auth": "none", "render": "static-html",
     "params": ["q", "page"], "status": "safe"},
    {"path": "/api/cart", "method": "POST", "auth": "user", "render": "spa-react",
     "params": ["sku"], "status": "safe"},
    {"path": "/api/orders/{id}", "method": "GET", "auth": "user", "render": "spa-react",
     "params": ["id"], "status": "safe"},
]


def build(make_catalog, entries=None, routes=SURFACE, app="shopfront"):
    root = make_catalog(entries if entries is not None else [vuln_entry()])
    routes_inventory(root, app, routes)
    return root


def test_inventory_loads_and_splits_planted_from_safe(make_catalog):
    inventories = load_inventories(build(make_catalog))
    inv = inventories["shopfront"]
    assert len(inv) == 4
    assert len(inv.planted) == 1 and len(inv.safe) == 3
    assert inv.match_template("GET", "/api/products") is not None
    # Dialects are normalised on both sides, as everywhere else.
    assert inv.match_template("GET", "/api/orders/:id") is not None
    assert inv.match_template("POST", "/api/products") is None


def test_literal_route_wins_over_a_parameterised_one(make_catalog):
    routes = [
        {"path": "/api/orders/{id}", "method": "GET", "status": "safe"},
        {"path": "/api/orders/export", "method": "GET", "status": "safe"},
    ]
    inv = load_inventories(build(make_catalog, routes=routes))["shopfront"]
    assert inv.match_path("GET", "/api/orders/export").path == "/api/orders/export"
    assert inv.match_path("GET", "/api/orders/42").path == "/api/orders/{id}"


def test_crosscheck_is_silent_when_nothing_is_published(make_catalog):
    catalog = load_catalog(make_catalog([vuln_entry()]))
    assert crosscheck_inventory(catalog, {}) == []


def test_planted_entrypoint_absent_from_the_inventory_is_an_error(make_catalog):
    root = build(make_catalog, routes=[r for r in SURFACE if r["status"] != "planted"])
    catalog = load_catalog(root)
    issues = crosscheck_inventory(catalog, load_inventories(root))
    assert [i.code for i in issues] == ["inventory-missing-entrypoint"]


def test_planted_flaw_declared_safe_is_an_error(make_catalog):
    routes = [dict(r, status="safe") for r in SURFACE]
    root = build(make_catalog, routes=routes)
    catalog = load_catalog(root)
    codes = [i.code for i in crosscheck_inventory(catalog, load_inventories(root))]
    # Worst case of the two directions: real detections there would be published
    # as false positives.
    assert "inventory-status-mismatch" in codes


def test_route_marked_planted_with_no_catalog_entry_is_an_error(make_catalog):
    routes = SURFACE + [{"path": "/api/legacy/import", "method": "POST", "status": "planted"}]
    root = build(make_catalog, routes=routes)
    catalog = load_catalog(root)
    codes = [i.code for i in crosscheck_inventory(catalog, load_inventories(root))]
    assert "inventory-planted-uncatalogued" in codes


def test_app_with_vulns_but_no_inventory_warns_once_others_publish(make_catalog):
    entries = [vuln_entry(id="BENCH-SHOP-0001", app="shopfront"),
               vuln_entry(id="BENCH-BANK-0001", app="bank")]
    root = build(make_catalog, entries=entries)
    catalog = load_catalog(root)
    issues = crosscheck_inventory(catalog, load_inventories(root))
    missing = [i for i in issues if i.code == "inventory-missing"]
    assert len(missing) == 1 and "bank" in missing[0].message


def test_bad_status_and_app_mismatch_are_errors(make_catalog, tmp_path):
    root = build(make_catalog, routes=[{"path": "/x", "method": "GET", "status": "maybe"}])
    (root / "targets" / "shopfront" / "routes.yaml").write_text(
        "app: shopfront-v2\nroutes:\n  - {path: /x, method: GET, status: maybe}\n",
        encoding="utf-8",
    )
    issues: list = []
    load_inventories(root, issues=issues)
    codes = {i.code for i in issues}
    assert {"inventory-app-mismatch", "inventory-bad-status"} <= codes


def test_crawl_coverage_reports_both_denominators(make_catalog):
    root = build(make_catalog)
    catalog = load_catalog(root)
    inventories = load_inventories(root)
    # The tool walks the planted endpoint and one safe one: perfect planted-only
    # recall, but only half the published surface.
    events = [http_event(params=[param("q", "x'")]),
              http_event(route="/api/catalog/items", params=[param("q", "shoes")])]
    doc = score_run(catalog, events_from_iterable(events), inventories=inventories)
    crawl = doc["metrics"]["crawl"]
    assert crawl["inventory_available"] is True
    assert doc["metrics"]["overall"]["reach"]["recall"] == 1.0   # biased denominator
    assert crawl["surface"] == {"routes": 4, "covered": 2, "coverage": 0.5}
    assert crawl["planted_routes"]["coverage"] == 1.0
    assert crawl["safe_routes"]["coverage"] == 1 / 3
    # Both numbers travel together so nobody has to reconcile two tables.
    assert crawl["planted_vuln_reach"] == doc["metrics"]["overall"]["reach"]
    assert crawl["by_render"]["spa-react"]["coverage"] == 0.0
    assert crawl["by_render"]["static-html"]["coverage"] == 1.0
    assert {r["path"] for r in crawl["unvisited_routes"]} == {"/api/cart", "/api/orders/{id}"}


def test_requests_outside_the_inventory_are_counted(make_catalog):
    root = build(make_catalog)
    doc = score_run(
        load_catalog(root),
        events_from_iterable([http_event(route="<unmatched>", path="/wp-admin")]),
        inventories=load_inventories(root),
    )
    assert doc["metrics"]["crawl"]["requests_off_inventory"] == 1


def test_no_inventory_leaves_the_crawl_block_empty_but_present(make_catalog):
    doc = score_run(load_catalog(make_catalog([vuln_entry()])), events_from_iterable([]))
    crawl = doc["metrics"]["crawl"]
    assert crawl["inventory_available"] is False
    assert crawl["surface"]["coverage"] is None  # null, not 0: no denominator


def test_finding_on_a_declared_safe_route_is_a_confirmed_false_positive(make_catalog):
    root = build(make_catalog)
    catalog = load_catalog(root)
    inventories = load_inventories(root)
    findings = [
        finding_from_dict({"tool": "zap", "url": "http://shopfront/api/products?q=1",
                           "method": "GET", "param": "q", "cwe": 89, "name": "SQLi"}),
        finding_from_dict({"tool": "zap", "url": "http://shopfront/api/catalog/items?q=1",
                           "method": "GET", "param": "q", "cwe": 89, "name": "SQLi"}),
        finding_from_dict({"tool": "zap", "url": "http://shopfront/undeclared/thing",
                           "method": "GET", "param": None, "cwe": 89, "name": "SQLi"}),
    ]
    report = classify_findings(catalog, findings, inventories=inventories,
                               app_map={"shopfront": "shopfront"})
    bases = [r["fp_basis"] for r in report["findings"]]
    assert bases == [None, "inventory-safe-route", "unknown-route"]
    assert report["false_positives"] == 2
    assert report["false_positives_confirmed"] == 1
    assert report["false_positives_unknown_route"] == 1
    assert report["precision"] == 1 / 3
    # Precision restricted to the surface we can actually defend route by route.
    assert report["precision_confirmed"] == 0.5


def test_fp_basis_is_no_inventory_when_none_is_published(make_catalog):
    catalog = load_catalog(make_catalog([vuln_entry()]))
    report = classify_findings(catalog, [finding_from_dict(
        {"tool": "zap", "url": "http://shopfront/nope", "method": "GET", "cwe": 89})])
    assert report["findings"][0]["fp_basis"] == "no-inventory"
    assert report["precision_confirmed"] is None


def test_coverage_summary_flags_the_decoy_ratio(make_catalog):
    summary = coverage_summary(load_inventories(build(make_catalog)))
    assert summary["routes"] == 4 and summary["planted"] == 1 and summary["safe"] == 3
    # The contract asks for at least three safe endpoints per planted one.
    assert summary["safe_per_planted"] == 3.0


def test_score_document_with_crawl_and_weak_attribution_matches_the_schema(make_catalog):
    import json

    from jsonschema import Draft202012Validator

    from conftest import REPO_ROOT, oob_event

    entries = [
        vuln_entry(),
        vuln_entry(id="BENCH-SHOP-0031", **{"class": "ssrf_blind"}, severity="high",
                   entrypoint={"method": "POST", "path": "/api/admin/imports",
                               "param": "source_url", "param_in": "json",
                               "default_value": "https://suppliers/catalog.json"},
                   oracle={"kind": "oob", "signal": "shop.imports.fetch.external",
                           "condition": "The importer fetched a caller-supplied URL."}),
    ]
    routes = SURFACE + [{"path": "/api/admin/imports", "method": "POST", "auth": "admin",
                         "render": "spa-react", "params": ["source_url"], "status": "planted"}]
    root = build(make_catalog, entries=entries, routes=routes)
    catalog = load_catalog(root)
    doc = score_run(
        catalog,
        events_from_iterable([
            http_event(params=[param("q", "x'")]),
            oob_event(signal="shop.imports.fetch.external", attribution="container-window"),
            oob_event(signal="nobody.claims.this.signal"),
        ]),
        run={"run_id": "r1", "tool": "acme"},
        inventories=load_inventories(root),
    )
    schema = json.loads((REPO_ROOT / "results" / "schema" / "score.schema.json").read_text())
    assert sorted(e.message for e in Draft202012Validator(schema).iter_errors(doc)) == []
    assert doc["schema_version"] == "1.1.0"
    assert doc["low_confidence_triggers"]["count"] == 1
    assert len(doc["low_confidence_triggers"]["unattributed_callbacks"]) == 1
