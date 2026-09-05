"""Route inventories: loading, catalog cross-checks, crawl coverage, FP denominator."""

from __future__ import annotations

from benchctl.catalog import load_catalog
from benchctl.events import events_from_iterable
from benchctl.findings import classify_findings, finding_from_dict
import pytest

from benchctl.inventory import (
    coverage_summary,
    crosscheck_inventory,
    host_matches,
    load_inventories,
    normalize_host,
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


# --------------------------------------------------------------------------- #
# virtual hosts
# --------------------------------------------------------------------------- #

VHOST_SURFACE = [
    # One container, three vhosts. /.git/config is exposed on www (the deployment
    # that went wrong) and correctly refused on the two hardened ones.
    {"path": "/.git/config", "method": "GET", "hosts": ["www"], "render": "static-html",
     "status": "planted"},
    {"path": "/.git/config", "method": "GET", "hosts": ["static", "docs"],
     "render": "static-html", "status": "safe"},
    {"path": "/index.html", "method": "GET", "hosts": ["www", "static", "docs"],
     "render": "static-html", "status": "safe"},
]


def vhost_catalog(make_catalog):
    entry = vuln_entry(
        id="BENCH-INFR-0001", app="infra", **{"class": "exposed_vcs"}, severity="high",
        entrypoint={"method": "GET", "path": "/.git/config", "param": None,
                    "default_value": None},
        discovery={"render": "static-html", "difficulty": 1},
        oracle={"kind": "artifact",
                "condition": "The repository object store was read by the caller."},
    )
    root = make_catalog([entry])
    routes_inventory(root, "infra", VHOST_SURFACE)
    return root


def test_a_row_listing_several_hosts_becomes_several_keys(make_catalog):
    inv = load_inventories(vhost_catalog(make_catalog))["infra"]
    assert inv.hosts == ("docs", "static", "www")
    assert inv.single_host is False
    assert len(inv.routes) == 6  # 1 + 2 + 3 host-expanded rows
    assert inv.match_path("GET", "/.git/config", host="www").status == "planted"
    assert inv.match_path("GET", "/.git/config", host="static").status == "safe"
    assert inv.planted_hosts("GET", "/.git/config") == ("www",)


def test_hosts_match_short_labels_and_fully_qualified_names(make_catalog):
    inv = load_inventories(vhost_catalog(make_catalog))["infra"]
    assert inv.resolve_host("www.northlakefab.com") == "www"
    assert inv.resolve_host("static.northlakefab.com:8080") == "static"
    assert inv.resolve_host("infra-web") is None  # the harness alias names no vhost


def test_the_exposed_vhost_is_a_true_positive_and_the_hardened_one_is_not(make_catalog):
    root = vhost_catalog(make_catalog)
    catalog = load_catalog(root)
    inventories = load_inventories(root)
    findings = [
        finding_from_dict({"tool": "zap", "url": "http://www.northlakefab.com/.git/config",
                           "method": "GET", "cwe": 527, "name": "Exposed .git"}),
        finding_from_dict({"tool": "zap", "url": "http://static.northlakefab.com/.git/config",
                           "method": "GET", "cwe": 527, "name": "Exposed .git"}),
    ]
    report = classify_findings(catalog, findings, inventories=inventories,
                               app_map={"www.northlakefab.com": "infra",
                                        "static.northlakefab.com": "infra"})
    exposed, hardened = report["findings"]
    assert exposed["verdict"] == "true-positive"
    assert exposed["host"] == "www.northlakefab.com" and exposed["host_match"] == "exact"
    # The hardened twin is a confirmed false positive, not a second true positive:
    # this is the distinction the target exists to test.
    assert hardened["verdict"] == "false-positive"
    assert hardened["fp_basis"] == "inventory-safe-route"
    assert report["precision"] == 0.5
    assert report["false_positives_confirmed"] == 1


def test_an_unresolvable_host_falls_back_and_says_so(make_catalog):
    root = vhost_catalog(make_catalog)
    report = classify_findings(
        load_catalog(root),
        [finding_from_dict({"tool": "zap", "url": "http://infra-web/.git/config",
                            "method": "GET", "cwe": 527, "name": "Exposed .git"})],
        inventories=load_inventories(root), app_map={"infra-web": "infra"},
    )
    row = report["findings"][0]
    assert row["verdict"] == "true-positive"
    assert row["host_match"] == "agnostic-host-unresolved"


def test_a_single_host_target_is_matched_host_agnostically(make_catalog):
    root = build(make_catalog)  # SURFACE declares no hosts
    report = classify_findings(
        load_catalog(root),
        [finding_from_dict({"tool": "zap", "url": "http://shopfront:8080/api/products?q=1",
                            "method": "GET", "param": "q", "cwe": 89, "name": "SQLi"})],
        inventories=load_inventories(root), app_map={"shopfront": "shopfront"},
    )
    assert report["findings"][0]["verdict"] == "true-positive"
    assert report["findings"][0]["host_match"] == "agnostic-single-host"


def test_two_rows_with_no_host_to_tell_them_apart_warn(make_catalog):
    root = make_catalog([vuln_entry()])
    routes_inventory(root, "shopfront", [
        {"path": "/api/products", "method": "GET", "status": "planted"},
        {"path": "/api/products", "method": "GET", "status": "safe"},
    ])
    issues: list = []
    load_inventories(root, issues=issues)
    ambiguous = [i for i in issues if i.code == "inventory-ambiguous-route"]
    assert ambiguous and "read last" in ambiguous[0].message


def test_distinct_hosts_are_not_ambiguous(make_catalog):
    issues: list = []
    load_inventories(vhost_catalog(make_catalog), issues=issues)
    assert [i for i in issues if i.code == "inventory-ambiguous-route"] == []


def test_refused_equivalents_are_read_as_safe_rows(make_catalog):
    root = make_catalog([vuln_entry(
        id="BENCH-INFR-0001", app="infra", **{"class": "exposed_vcs"}, severity="high",
        entrypoint={"method": "GET", "path": "/.git/config", "param": None,
                    "default_value": None},
        oracle={"kind": "artifact", "condition": "The repository object store was read."})])
    (root / "targets" / "infra").mkdir(parents=True, exist_ok=True)
    (root / "targets" / "infra" / "routes.yaml").write_text(
        "app: infra\n"
        "routes:\n"
        "  - {path: /.git/config, method: GET, hosts: [www], status: planted}\n"
        "refused_equivalents:\n"
        "  - {host: static, path: /.git/config, expect: 403, note: denied by the host}\n",
        encoding="utf-8")
    inv = load_inventories(root)["infra"]
    # The section a target had to invent while the key ignored the host now counts
    # for coverage and precision instead of being invisible.
    hardened = inv.match_path("GET", "/.git/config", host="static")
    assert hardened.status == "safe"
    assert hardened.origin == "refused_equivalents"
    assert hardened.expect_status == 403


def test_coverage_says_when_it_cannot_tell_vhosts_apart(make_catalog):
    root = vhost_catalog(make_catalog)
    doc = score_run(load_catalog(root),
                    events_from_iterable([http_event(app="infra", route="/.git/config")]),
                    inventories=load_inventories(root))
    crawl = doc["metrics"]["crawl"]
    # Requests carry no vhost today, so one visit credits all three rows; the
    # inflation is reported rather than hidden.
    assert crawl["host_resolution"] == "collapsed"
    # Both /.git/config (3 rows) and /index.html (3 rows) collapse.
    assert crawl["rows_sharing_a_route_across_hosts"] == 6
    assert crawl["hosts"] == ["docs", "static", "www"]
    assert crawl["surface"]["covered"] == 3

    # When an SDK does report the vhost, only that host's row is credited.
    exact = score_run(load_catalog(root),
                      events_from_iterable([http_event(app="infra", route="/.git/config",
                                                       host="www.northlakefab.com")]),
                      inventories=load_inventories(root))
    assert exact["metrics"]["crawl"]["host_resolution"] == "host-aware"
    assert exact["metrics"]["crawl"]["surface"]["covered"] == 1
    assert exact["metrics"]["crawl"]["by_host"]["www"]["covered"] == 1
    assert exact["metrics"]["crawl"]["by_host"]["static"]["covered"] == 0


# --------------------------------------------------------------------------- #
# host normalisation (SDK drift must not move a score)
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "raw,expected",
    [
        # IPv6: the Node SDK keeps the brackets a URL authority carries, the Python
        # SDK unwraps them to the form a resolver deals in. Both fold to one host.
        ("[2001:db8::1]", "2001:db8::1"),
        ("2001:db8::1", "2001:db8::1"),
        ("[2001:db8::1]:8080", "2001:db8::1"),
        ("[::1]", "::1"),
        ("::1", "::1"),
        # case is folded, port is stripped, trailing dot is stripped for matching
        ("WWW.Example.COM", "www.example.com"),
        ("www.example.com.", "www.example.com"),
        ("shopfront:3000", "shopfront"),
        ("10.88.0.9:8080", "10.88.0.9"),
        # not a port: a colon followed by a name is part of the host
        ("weird:name", "weird:name"),
        ("", None),
        (None, None),
    ],
)
def test_host_normalisation_table(raw, expected):
    assert normalize_host(raw) == expected


@pytest.mark.parametrize(
    "a,b",
    [
        ("[2001:db8::1]", "2001:db8::1"),
        ("2001:db8::1", "[2001:db8::1]"),
        ("[2001:db8::1]:443", "2001:db8::1"),
        ("2001:db8::1", "[2001:db8::1]:8080"),
        ("example.com", "example.com."),
        ("example.com.", "EXAMPLE.com"),
        ("www", "www.northlakefab.com"),
    ],
)
def test_hosts_that_must_compare_equal(a, b):
    # Symmetric on purpose: the divergence can appear on either side, since the
    # inventory is written by a human and the event by whichever SDK ships next.
    assert host_matches(a, b)
    assert host_matches(b, a)


@pytest.mark.parametrize(
    "a,b",
    [
        ("2001:db8::1", "2001:db8::2"),
        ("[2001:db8::1]", "[2001:db8::2]"),
        # Two addresses sharing a first label are two hosts; the short-label
        # heuristic that lets `www` match `www.example.com` must not apply to them.
        ("192.168.0.1", "192.168.0.2"),
        ("::ffff:192.168.0.1", "::ffff:192.168.0.2"),
        ("www", "static.northlakefab.com"),
    ],
)
def test_hosts_that_must_not_compare_equal(a, b):
    assert not host_matches(a, b)
    assert not host_matches(b, a)


def test_an_ipv6_inventory_row_matches_either_sdk_spelling(make_catalog):
    root = make_catalog([vuln_entry(id="BENCH-INFR-0001", app="infra",
                                    **{"class": "exposed_vcs"}, severity="high",
                                    entrypoint={"method": "GET", "path": "/.git/config",
                                                "param": None, "default_value": None},
                                    oracle={"kind": "artifact",
                                            "condition": "The repository object store was read."})])
    routes_inventory(root, "infra", [
        {"path": "/.git/config", "method": "GET", "hosts": ["[2001:db8::1]"], "status": "planted"},
        {"path": "/.git/config", "method": "GET", "hosts": ["2001:db8::2"], "status": "safe"},
    ])
    inv = load_inventories(root)["infra"]
    # Declared bracketed, observed bare (Python SDK) and vice versa (Node SDK).
    assert inv.match_path("GET", "/.git/config", host="2001:db8::1").status == "planted"
    assert inv.match_path("GET", "/.git/config", host="[2001:db8::2]").status == "safe"
    assert inv.resolve_host("[2001:db8::1]:8080") == "2001:db8::1"


def test_the_raw_host_is_kept_as_evidence_beside_the_normalised_one(make_catalog):
    root = build(make_catalog)
    report = classify_findings(
        load_catalog(root),
        [finding_from_dict({"tool": "zap", "url": "http://ShopFront.:8080/api/products?q=1",
                            "method": "GET", "param": "q", "cwe": 89, "name": "SQLi"})],
        inventories=load_inventories(root), app_map={"shopfront": "shopfront"},
    )
    row = report["findings"][0]
    assert row["host"] == "shopfront"                    # what the verdict used
    assert row["host_observed"] == "ShopFront.:8080"     # what was on the wire
