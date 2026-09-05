"""False-positive classifier and precision arithmetic."""

from __future__ import annotations

import json

from benchctl.catalog import load_catalog
from benchctl.events import events_from_iterable
from benchctl.findings import (
    VERDICT_CLASS_MISMATCH,
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
    # CWE-79 (XSS) claimed on the SQL injection endpoint: neither TP nor FP.
    report = classify_findings(catalog_of(make_catalog), [finding(cwe=79, name="XSS")])
    assert verdicts(report) == [VERDICT_CLASS_MISMATCH]
    assert report["true_positives"] == 0 and report["false_positives"] == 0
    assert report["ambiguous"] == 1
    assert report["precision"] is None            # nothing to divide: 0 TP, 0 FP
    assert report["precision_conservative"] == 0.0  # hostile reading counts it against


def test_missing_cwe_is_class_unknown(make_catalog):
    report = classify_findings(catalog_of(make_catalog), [finding(cwe=None)])
    assert verdicts(report) == [VERDICT_CLASS_UNKNOWN]


def test_unmapped_cwe_is_a_class_mismatch(make_catalog):
    report = classify_findings(catalog_of(make_catalog), [finding(cwe=1234567)])
    assert verdicts(report) == [VERDICT_CLASS_MISMATCH]
    assert "unmapped" in report["findings"][0]["reason"]


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
        finding(cwe=79),                             # ambiguous (class mismatch)
    ]
    report = classify_findings(catalog_of(make_catalog), findings)
    assert (report["true_positives"], report["false_positives"],
            report["duplicates"], report["ambiguous"]) == (2, 2, 1, 1)
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
