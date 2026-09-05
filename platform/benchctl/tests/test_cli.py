"""End-to-end CLI behaviour, including exit codes (CI depends on them)."""

from __future__ import annotations

import json

import pytest

from benchctl.cli import main
from conftest import REPO_ROOT, http_event, param, trigger_event, vuln_entry


def write_json(path, payload):
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


@pytest.fixture
def run_root(make_catalog):
    entries = [
        vuln_entry(id="BENCH-SHOP-0001"),
        vuln_entry(id="BENCH-SHOP-0002", **{"class": "bola"}, severity="critical",
                   entrypoint={"method": "GET", "path": "/api/orders/:id", "auth": "other-user",
                               "param": "id", "param_in": "path", "default_value": "1001"},
                   discovery={"render": "spa-react", "requires": ["js-execution"], "difficulty": 3}),
    ]
    return make_catalog(entries)


# Targets land continuously and several teams write the tree at once, so a test
# that demanded a globally green repository would fail on somebody else's half-
# landed file rather than on a defect here. These assert what this package owns:
# the catalog itself is internally consistent, and anything else it reports is a
# cross-component disagreement it is designed to surface.
_CROSSCHECK_CODES = {
    "inventory-missing-entrypoint", "inventory-planted-uncatalogued",
    "inventory-status-mismatch", "inventory-app-mismatch", "inventory-bad-status",
    "inventory-parse", "inventory-shape", "inventory-duplicate-app",
}


def test_validate_runs_on_the_shipped_catalog(capsys):
    code = main(["--root", str(REPO_ROOT), "validate"])
    out = capsys.readouterr()
    assert "error(s)" in out.out
    assert code in (0, 1)


def test_shipped_catalog_has_no_internal_errors(capsys):
    assert main(["--root", str(REPO_ROOT), "validate", "--json"]) in (0, 1)
    payload = json.loads(capsys.readouterr().out)
    assert payload["digest"]
    offenders = {e["code"] for e in payload["errors"]} - _CROSSCHECK_CODES
    assert offenders == set(), f"catalog-internal errors: {offenders}"


def test_validate_exits_non_zero_on_a_broken_catalog(make_catalog, capsys):
    root = make_catalog([vuln_entry(**{"class": "does_not_exist"})])
    assert main(["--root", str(root), "validate"]) == 1
    assert "unknown-class" in capsys.readouterr().err


def test_score_end_to_end(run_root, tmp_path, capsys):
    events = write_json(tmp_path / "events.json", {
        "run": {"run_id": "r1", "tool": "zap", "tool_version": "2.15", "targets": ["shopfront"]},
        "events": [
            http_event(params=[param("q", "' OR 1=1--")]),
            trigger_event("BENCH-SHOP-0001"),
            http_event(route="/api/orders/{id}", params=[param("id", "1001", "path")]),
            http_event(params=[param("q", "seed")], synthetic=True),
        ],
    })
    out = tmp_path / "score.json"
    assert main(["--root", str(run_root), "score", "--run", "r1",
                 "--events", events, "--out", str(out)]) == 0
    doc = json.loads(out.read_text())
    assert doc["run"]["tool"] == "zap"
    assert doc["metrics"]["overall"]["reach"]["recall"] == 1.0
    assert doc["metrics"]["overall"]["trigger"]["hit"] == 1
    # /api/orders replayed the default id -> reached but not exercised
    by_id = {v["id"]: v for v in doc["vulns"]}
    assert by_id["BENCH-SHOP-0002"]["exercise"] is False
    printed = capsys.readouterr().out
    assert "reach" in printed and "trigger" in printed


def test_score_with_findings_adds_precision(run_root, tmp_path):
    events = write_json(tmp_path / "e.json", [http_event(params=[param("q", "x'")]),
                                              trigger_event("BENCH-SHOP-0001")])
    findings = write_json(tmp_path / "f.json", [
        {"tool": "zap", "url": "http://shopfront:8080/api/products?q=1", "method": "GET",
         "param": "q", "cwe": 89, "name": "SQLi", "severity": "high", "confidence": "high"},
        {"tool": "zap", "url": "http://shopfront:8080/robots.txt", "method": "GET",
         "param": None, "cwe": 200, "name": "Info leak", "severity": "low", "confidence": "low"},
    ])
    out = tmp_path / "score.json"
    assert main(["--root", str(run_root), "score", "--run", "r1", "--events", events,
                 "--findings", findings, "--out", str(out)]) == 0
    doc = json.loads(out.read_text())
    assert doc["findings"]["precision"] == 0.5
    assert doc["findings"]["triggered_not_reported"] == []


def test_score_refuses_to_run_against_a_broken_catalog(make_catalog, tmp_path, capsys):
    root = make_catalog([vuln_entry(**{"class": "does_not_exist"})])
    events = write_json(tmp_path / "e.json", [])
    assert main(["--root", str(root), "score", "--run", "r1", "--events", events,
                 "--out", str(tmp_path / "s.json")]) == 2
    assert "refusing to score" in capsys.readouterr().err
    # ...unless the operator says so explicitly.
    assert main(["--root", str(root), "score", "--run", "r1", "--events", events,
                 "--ignore-catalog-errors", "--out", str(tmp_path / "s.json")]) == 0


def test_score_overrides_and_app_scope(run_root, tmp_path):
    events = write_json(tmp_path / "e.json", [http_event()])
    out = tmp_path / "s.json"
    assert main(["--root", str(run_root), "score", "--run", "r9", "--events", events,
                 "--tool", "burp", "--tool-version", "2026.1", "--profile", "audit",
                 "--apps", "shopfront", "--out", str(out)]) == 0
    doc = json.loads(out.read_text())
    assert (doc["run"]["tool"], doc["run"]["tool_version"], doc["run"]["profile"]) == (
        "burp", "2026.1", "audit")
    assert doc["catalog"]["vulns_in_scope"] == 2


def test_report_renders_several_runs(run_root, tmp_path):
    docs = []
    for tool in ("zap", "burp"):
        events = write_json(tmp_path / f"{tool}-e.json", [http_event()])
        out = tmp_path / f"{tool}.json"
        assert main(["--root", str(run_root), "score", "--run", tool, "--events", events,
                     "--tool", tool, "--out", str(out)]) == 0
        docs.append(str(out))
    assert main(["--root", str(run_root), "report", "--runs", ",".join(docs),
                 "--out", str(tmp_path / "results")]) == 0
    md = (tmp_path / "results" / "results.md").read_text()
    assert "| zap |" in md and "| burp |" in md
    assert (tmp_path / "results" / "results.html").exists()


def test_report_resolves_run_ids_under_results_dir(run_root, tmp_path):
    events = write_json(tmp_path / "e.json", [http_event()])
    results = tmp_path / "results"
    assert main(["--root", str(run_root), "score", "--run", "r1", "--events", events,
                 "--out", str(results / "runs" / "r1" / "score.json")]) == 0
    assert main(["--root", str(run_root), "report", "--runs", "r1",
                 "--results-dir", str(results), "--out", str(tmp_path / "out")]) == 0
    assert (tmp_path / "out" / "results.json").exists()


def test_report_fails_loudly_on_an_unknown_run(run_root, tmp_path):
    with pytest.raises(SystemExit):
        main(["--root", str(run_root), "report", "--runs", "ghost",
              "--results-dir", str(tmp_path), "--out", str(tmp_path / "o")])


def test_catalog_stats_lists_empty_cells(run_root, capsys):
    assert main(["--root", str(run_root), "catalog", "stats"]) == 0
    out = capsys.readouterr().out
    assert "planted vulnerabilities" in out
    assert "<- EMPTY" in out
    assert "zero planted vulnerabilities" in out


def test_catalog_stats_json(run_root, capsys):
    assert main(["--root", str(run_root), "catalog", "stats", "--json"]) == 0
    stats = json.loads(capsys.readouterr().out)
    assert stats["total_vulns"] == 2
    assert "xss_stored" in stats["empty_classes"]
    assert stats["owasp"]["2025"]["counts"]["A01"] == 1


def test_validate_reports_inventory_disagreements(make_catalog, capsys):
    from conftest import routes_inventory

    root = make_catalog([vuln_entry()])
    # The inventory calls the planted route safe: real detections there would be
    # published as false positives, so validate must go red.
    routes_inventory(root, "shopfront", [
        {"path": "/api/products", "method": "GET", "status": "safe"},
        {"path": "/api/legacy", "method": "POST", "status": "planted"},
    ])
    assert main(["--root", str(root), "validate"]) == 1
    err = capsys.readouterr().err
    assert "inventory-status-mismatch" in err
    assert "inventory-planted-uncatalogued" in err


def test_score_reports_crawl_coverage_and_weak_attributions(run_root, tmp_path, capsys):
    from conftest import oob_event, routes_inventory

    routes_inventory(run_root, "shopfront", [
        {"path": "/api/products", "method": "GET", "render": "static-html", "status": "planted"},
        {"path": "/api/orders/{id}", "method": "GET", "render": "spa-react", "status": "planted"},
        {"path": "/api/catalog/items", "method": "GET", "render": "static-html", "status": "safe"},
        {"path": "/api/cart", "method": "POST", "render": "spa-react", "status": "safe"},
    ])
    events = write_json(tmp_path / "e.json", [
        http_event(params=[param("q", "x'")]),
        trigger_event("BENCH-SHOP-0001"),
        oob_event(signal="shop.synthetic.shop_0002.anomaly", attribution="container-window"),
    ])
    out = tmp_path / "score.json"
    assert main(["--root", str(run_root), "score", "--run", "r1", "--events", events,
                 "--out", str(out)]) == 0
    doc = json.loads(out.read_text())
    assert doc["metrics"]["crawl"]["surface"] == {"routes": 4, "covered": 1, "coverage": 0.25}
    assert doc["metrics"]["overall"]["trigger"]["hit"] == 1
    assert doc["metrics"]["overall"]["trigger_any"]["hit"] == 2
    assert doc["low_confidence_triggers"]["credited_only_here"] == ["BENCH-SHOP-0002"]
    printed = capsys.readouterr().out
    assert "trig+weak" in printed
    assert "of the published surface" in printed
    assert "weak-oob" in printed


def test_catalog_stats_lists_surface_and_missing_signals(run_root, capsys):
    from conftest import routes_inventory

    routes_inventory(run_root, "shopfront", [
        {"path": "/api/products", "method": "GET", "status": "planted"},
        {"path": "/api/orders/{id}", "method": "GET", "status": "planted"},
        {"path": "/api/catalog/items", "method": "GET", "status": "safe"},
    ])
    assert main(["--root", str(run_root), "catalog", "stats"]) == 0
    out = capsys.readouterr().out
    assert "published surface: 3 routes" in out
    # 1 safe route for 2 planted ones: well under the 3:1 the contract asks for,
    # which is exactly the kind of gap this listing exists to surface.
    assert "0.5 safe per planted" in out


def test_catalog_stats_json_carries_oracle_and_surface_queues(run_root, capsys):
    assert main(["--root", str(run_root), "catalog", "stats", "--json"]) == 0
    stats = json.loads(capsys.readouterr().out)
    assert stats["oracles"]["with_signal"] == 2
    assert stats["oracles"]["without_signal"] == []
    assert stats["surface"]["routes"] == 0


def test_catalog_stats_prints_the_roadmap_gap(run_root, capsys):
    assert main(["--root", str(run_root), "catalog", "stats"]) == 0
    out = capsys.readouterr().out
    assert "roadmap (" in out
    # The prefix column is the roadmap's, so a non-derivable one must appear as it
    # is written there rather than as a derivation from the app name.
    assert "admin        ADMN" in out
    assert "to go" in out


def test_validate_accepts_a_non_derivable_prefix(make_catalog, capsys):
    root = make_catalog([vuln_entry(id="BENCH-ADMN-0001", app="admin",
                                    **{"class": "el_injection"}, severity="critical")])
    assert main(["--root", str(root), "validate"]) == 0
    assert "id-app-mismatch" not in capsys.readouterr().err
