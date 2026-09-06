"""False-positive classifier and precision arithmetic."""

from __future__ import annotations

import json

from benchctl.catalog import load_catalog
from benchctl.events import events_from_iterable
from benchctl.findings import (
    VERDICT_CLASS_MISMATCH,
    VERDICT_OUT_OF_CATALOG,
    VERDICT_CLASS_UNKNOWN,
    VERDICT_DUP,
    VERDICT_FP,
    VERDICT_PARAM_MISMATCH,
    VERDICT_TP,
    classify_findings,
    finding_from_dict,
    load_findings,
    parse_cwes,
)
from benchctl.scoring import score_run
from conftest import http_event, param, trigger_event, vuln_entry

BASE = "http://shopfront:8080"


def catalog_of(make_catalog):
    entries = [
        vuln_entry(id="BENCH-SHOP-0001"),  # sqli_union, GET /api/products, param q, CWE 89
        vuln_entry(id="BENCH-SHOP-0002", **{"class": "bola"}, severity="critical",
                   entrypoint={"method": "GET", "path": "/api/orders/:id", "auth": "other-user",
                               "param": "id", "param_in": "path", "default_value": "1001"}),
    ]
    return load_catalog(make_catalog(entries))


def finding(**kw):
    base = {"tool": "zap", "url": f"{BASE}/api/products?q=1", "method": "GET",
            "param": "q", "cwe": 89, "name": "SQL injection", "severity": "high",
            "confidence": "medium"}
    base.update(kw)
    return finding_from_dict(base)


def verdicts(report):
    return [row["verdict"] for row in report["findings"]]


def test_exact_cwe_on_the_right_location_is_a_true_positive(make_catalog):
    report = classify_findings(catalog_of(make_catalog), [finding()])
    assert verdicts(report) == [VERDICT_TP]
    assert report["findings"][0]["matched_vuln"] == "BENCH-SHOP-0001"
    assert report["precision"] == 1.0


def test_concrete_url_matches_a_parameterised_template(make_catalog):
    f = finding(url=f"{BASE}/api/orders/1002", param="id", cwe="CWE-639")
    report = classify_findings(catalog_of(make_catalog), [f])
    assert report["findings"][0]["matched_vuln"] == "BENCH-SHOP-0002"
    assert verdicts(report) == [VERDICT_TP]


def test_related_cwe_in_the_same_family_still_counts(make_catalog):
    # CWE-943 (NoSQL) against a CWE-89 (SQL) vulnerability: both `injection`.
    report = classify_findings(catalog_of(make_catalog), [finding(cwe=943)])
    assert verdicts(report) == [VERDICT_TP]
    assert "cwe-family" in report["findings"][0]["reason"]


def test_wrong_location_is_a_false_positive(make_catalog):
    report = classify_findings(catalog_of(make_catalog), [finding(url=f"{BASE}/api/健康?q=1")])
    assert verdicts(report) == [VERDICT_FP]
    assert report["precision"] == 0.0
    assert report["false_positive_list"][0]["url"].endswith("q=1")


def test_wrong_method_on_the_right_path_is_a_false_positive(make_catalog):
    report = classify_findings(catalog_of(make_catalog), [finding(method="POST")])
    assert verdicts(report) == [VERDICT_FP]


def test_right_place_wrong_class_is_reported_separately(make_catalog):
    # CWE-639 (IDOR) claimed on the SQL injection endpoint: neither TP nor FP. The
    # class is planted in scope -- on another endpoint -- so we can genuinely say
    # this is the wrong class here, rather than having no ground truth for it.
    report = classify_findings(catalog_of(make_catalog), [finding(cwe=639, name="IDOR")])
    assert verdicts(report) == [VERDICT_CLASS_MISMATCH]
    assert report["true_positives"] == 0 and report["false_positives"] == 0
    assert report["ambiguous"] == 1
    assert report["precision"] is None            # nothing to divide: 0 TP, 0 FP
    assert report["precision_conservative"] == 0.0  # hostile reading counts it against


def test_missing_cwe_is_class_unknown(make_catalog):
    report = classify_findings(catalog_of(make_catalog), [finding(cwe=None)])
    assert verdicts(report) == [VERDICT_CLASS_UNKNOWN]


def test_a_cwe_no_class_plants_is_unscoreable_even_on_a_planted_location(make_catalog):
    # Location is irrelevant here: no entry in the corpus could ever carry this CWE,
    # so the finding is outside the ground truth rather than wrong.
    report = classify_findings(catalog_of(make_catalog), [finding(cwe=1234567)])
    assert verdicts(report) == [VERDICT_OUT_OF_CATALOG]
    assert report["true_positives"] == 0 and report["false_positives"] == 0
    assert report["out_of_catalog"] == 1
    assert report["precision"] is None  # nothing scoreable to divide


def test_a_mixed_cwe_finding_is_still_judged_on_its_scoreable_half(make_catalog):
    # One planted CWE among unplanted ones: we can still say something, so we do.
    report = classify_findings(catalog_of(make_catalog), [finding(cwe=[693, 89])])
    assert verdicts(report) == [VERDICT_TP]


def test_wrong_parameter_on_the_right_endpoint(make_catalog):
    report = classify_findings(catalog_of(make_catalog), [finding(param="sort")])
    assert verdicts(report) == [VERDICT_PARAM_MISMATCH]


def test_finding_without_a_parameter_is_tolerated(make_catalog):
    # Endpoint-granularity reporting is a style, not an error.
    report = classify_findings(catalog_of(make_catalog), [finding(param=None)])
    assert verdicts(report) == [VERDICT_TP]


def test_second_report_of_the_same_vuln_is_a_duplicate(make_catalog):
    report = classify_findings(catalog_of(make_catalog), [finding(), finding(), finding()])
    assert verdicts(report) == [VERDICT_TP, VERDICT_DUP, VERDICT_DUP]
    assert report["true_positives"] == 1 and report["duplicates"] == 2
    assert report["precision"] == 1.0  # duplicates are noise, not inaccuracy
    assert report["duplicate_ratio"] == 2 / 3


def test_duplicates_are_per_tool(make_catalog):
    report = classify_findings(catalog_of(make_catalog), [finding(), finding(tool="nuclei")])
    assert verdicts(report) == [VERDICT_TP, VERDICT_TP]


def test_best_candidate_wins_on_a_shared_endpoint(make_catalog):
    # Two vulnerabilities on one endpoint: the finding must be judged against the
    # one it actually describes, whatever the file order.
    entries = [
        vuln_entry(id="BENCH-SHOP-0001"),
        vuln_entry(id="BENCH-SHOP-0002", **{"class": "xss_reflected"}, severity="medium",
                   entrypoint={"path": "/api/products", "param": "q", "default_value": "laptop"}),
    ]
    catalog = load_catalog(make_catalog(entries))
    report = classify_findings(catalog, [finding(cwe=79)])
    assert verdicts(report) == [VERDICT_TP]
    assert report["findings"][0]["matched_vuln"] == "BENCH-SHOP-0002"


def test_app_map_scopes_findings(make_catalog):
    entries = [vuln_entry(id="BENCH-SHOP-0001", app="shopfront"),
               vuln_entry(id="BENCH-BANK-0001", app="bank")]
    catalog = load_catalog(make_catalog(entries))
    f = finding(url="http://bank.test:9000/api/products?q=1")
    report = classify_findings(catalog, [f], app_map={"bank.test:9000": "bank"})
    assert report["findings"][0]["app"] == "bank"
    assert report["findings"][0]["matched_vuln"] == "BENCH-BANK-0001"


def test_precision_arithmetic_with_a_mixed_batch(make_catalog):
    findings = [
        finding(),                                   # TP
        finding(),                                   # duplicate
        finding(url=f"{BASE}/api/orders/1002", param="id", cwe=639),  # TP
        finding(url=f"{BASE}/nope"),                 # FP
        finding(url=f"{BASE}/also-nope"),            # FP
        finding(cwe=639, param=None),                # ambiguous (class mismatch)
        finding(cwe=693),                            # unscoreable, excluded entirely
    ]
    report = classify_findings(catalog_of(make_catalog), findings)
    assert (report["true_positives"], report["false_positives"], report["duplicates"],
            report["ambiguous"], report["out_of_catalog"]) == (2, 2, 1, 1, 1)
    assert report["precision"] == 0.5
    assert report["precision_conservative"] == 2 / 5


def test_cross_check_against_exploitation(make_catalog):
    catalog = catalog_of(make_catalog)
    doc = score_run(catalog, events_from_iterable([
        http_event(params=[param("q", "x'")]), trigger_event("BENCH-SHOP-0001"),
        trigger_event("BENCH-SHOP-0002"),
    ]))
    report = classify_findings(catalog, [finding()], outcomes=doc["vulns"])
    assert report["triggered_not_reported"] == ["BENCH-SHOP-0002"]
    assert report["reported_not_triggered"] == []


def test_parse_cwes_accepts_every_dialect():
    assert parse_cwes(89) == (89,)
    assert parse_cwes("89") == (89,)
    assert parse_cwes("CWE-89") == (89,)
    assert parse_cwes(["CWE-89", 943, "cwe_89"]) == (89, 943)
    assert parse_cwes(None) == ()
    assert parse_cwes("none") == ()


def test_load_findings_accepts_array_and_object(tmp_path):
    rows = [{"tool": "zap", "url": "/a", "cwe": 89}]
    p1 = tmp_path / "a.json"
    p1.write_text(json.dumps(rows))
    p2 = tmp_path / "b.json"
    p2.write_text(json.dumps({"findings": rows}))
    assert len(load_findings(p1)) == len(load_findings(p2)) == 1


# --------------------------------------------------------------------------- #
# regression: the first live scanner run
# --------------------------------------------------------------------------- #

def zap_baseline_findings():
    """The shape ZAP's baseline actually produced against `blog`: 21 findings, all
    real, none of them a class this corpus plants."""
    rows = []
    for i in range(10):  # "Content Security Policy Header Not Set"
        rows.append({"tool": "zap", "url": f"http://blog:8080/page{i}", "method": "GET",
                     "param": None, "cwe": 693, "name": "CSP Header Not Set",
                     "severity": "medium", "confidence": "high"})
    for path in ("/account", "/studio", "/privacy", "/", "/feed"):  # anti-clickjacking
        rows.append({"tool": "zap", "url": f"http://blog:8080{path}", "method": "GET",
                     "param": None, "cwe": 1021, "name": "Missing Anti-clickjacking Header",
                     "severity": "medium", "confidence": "medium"})
    for path in ("/a", "/b", "/c", "/d", "/e", "/f"):  # informational, no CWE at all
        rows.append({"tool": "zap", "url": f"http://blog:8080{path}", "method": "GET",
                     "param": None, "cwe": None, "name": "Informational",
                     "severity": "info", "confidence": "low"})
    return [finding_from_dict(r) for r in rows]


def test_a_scanner_that_said_nothing_false_is_not_published_at_zero_precision(make_catalog):
    # The regression this test exists for: all 21 findings were classified as false
    # positives and the tool was published at precision 0.0% in a public comparison.
    report = classify_findings(catalog_of(make_catalog), zap_baseline_findings(),
                               app_map={"blog": "blog"})
    # 10 CSP alerts (no class in the taxonomy plants CWE-693) plus 5 clickjacking
    # alerts (a class exists, but nothing in the scanned app carries it): 15
    # findings this corpus has no ground truth for, in either direction.
    assert report["out_of_catalog"] == 15
    assert report["out_of_catalog_by_kind"] == {"unplanted-class": 10,
                                                "unplanted-in-scope": 5}
    assert report["out_of_catalog_by_cwe"]["693"]["count"] == 10
    assert report["false_positives_confirmed"] == 0       # nothing contradicted
    # The headline must not be 0%: it is "we cannot say", which is the truth.
    assert report["precision_confirmed"] is None
    assert all(r["verdict"] != "false-positive"
               for r in report["findings"] if r["cwe"])


def test_out_of_catalog_findings_leave_every_denominator_alone(make_catalog):
    catalog = catalog_of(make_catalog)
    clean = classify_findings(catalog, [finding()])
    polluted = classify_findings(catalog, [finding(), *zap_baseline_findings()[:10]])
    assert polluted["out_of_catalog"] == 10
    assert polluted["precision"] == clean["precision"] == 1.0
    assert polluted["precision_conservative"] == clean["precision_conservative"] == 1.0


def test_a_reason_is_given_for_each_unplanted_cwe(make_catalog):
    report = classify_findings(catalog_of(make_catalog), zap_baseline_findings()[:1],
                               out_of_catalog_reasons={693: "header hygiene, not planted"})
    assert report["out_of_catalog_by_cwe"]["693"]["reason"] == "header hygiene, not planted"
    assert "unscoreable, not wrong" in report["findings"][0]["reason"]


def test_the_real_reason_table_is_read_when_present():
    from benchctl.findings import load_out_of_catalog_reasons
    from conftest import REPO_ROOT

    reasons = load_out_of_catalog_reasons(REPO_ROOT)
    # runners/_lib/cwe_map.yaml is the harness's own list; if it moves, we degrade
    # to a generic sentence rather than failing to score.
    assert reasons == {} or 693 in reasons
    assert load_out_of_catalog_reasons(None) == {}


def test_each_unscoreable_cwe_carries_the_right_reason(make_catalog):
    report = classify_findings(catalog_of(make_catalog), zap_baseline_findings(),
                               app_map={"blog": "blog"},
                               out_of_catalog_reasons={693: "header hygiene, not planted"})
    by_cwe = report["out_of_catalog_by_cwe"]
    assert by_cwe["693"]["reason"] == "header hygiene, not planted"
    # CWE-1021 is a real taxonomy class; the honest statement is that nothing in the
    # scanned app carries it, not that no class does.
    assert "nothing in" in by_cwe["1021"]["reason"]


def test_a_pattern_row_cannot_confirm_a_false_positive(make_catalog, tmp_path):
    # The SPA fallback /{full_path} answers every path, 404s included, so matching
    # it proves nothing about the path a tool reported.
    from benchctl.inventory import load_inventories
    from conftest import routes_inventory

    root = make_catalog([vuln_entry()])
    routes_inventory(root, "shopfront", [
        {"path": "/api/products", "method": "GET", "status": "planted"},
        {"path": "/about", "method": "GET", "status": "safe"},
        {"path": "/{full_path}", "method": "GET", "status": "safe"},
    ])
    catalog = load_catalog(root)
    inventories = load_inventories(root)
    report = classify_findings(
        catalog,
        [finding(url=f"{BASE}/whatever-the-spa-renders", param=None),
         finding(url=f"{BASE}/about", param=None)],
        inventories=inventories, app_map={"shopfront": "shopfront"},
    )
    pattern, literal = report["findings"]
    assert pattern["fp_basis"] == "inventory-pattern-route"
    assert literal["fp_basis"] == "inventory-safe-route"
    assert report["false_positives"] == 2
    assert report["false_positives_confirmed"] == 1      # only the literal row
    assert report["precision_confirmed"] == 0.0
