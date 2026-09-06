"""Rendering: markdown tables, offline HTML dashboard, N tools in one table."""

from __future__ import annotations

import json

from benchctl.catalog import load_catalog
from benchctl.events import events_from_iterable
from benchctl.report import html_report, load_score, markdown_report, tool_name, write_report
from benchctl.scoring import score_run
from conftest import http_event, param, trigger_event, vuln_entry


def two_runs(make_catalog):
    entries = [
        vuln_entry(id="BENCH-SHOP-0001"),  # static-html, injection
        vuln_entry(id="BENCH-SHOP-0002", **{"class": "xss_dom"}, severity="medium",
                   entrypoint={"method": "GET", "path": "/search", "param": "term",
                               "default_value": "shoes"},
                   discovery={"render": "spa-react", "requires": ["js-execution"], "difficulty": 4}),
    ]
    catalog = load_catalog(make_catalog(entries))
    good = score_run(
        catalog,
        events_from_iterable([
            http_event(params=[param("q", "x' OR 1=1--")]),
            trigger_event("BENCH-SHOP-0001"),
            http_event(route="/search", params=[param("term", "<svg onload=1>")]),
            trigger_event("BENCH-SHOP-0002"),
        ]),
        run={"run_id": "r-good", "tool": "acme-ptaas", "tool_version": "3.1", "profile": "full"},
    )
    crawler = score_run(
        catalog,
        events_from_iterable([http_event(params=[param("q", "laptop")])]),
        run={"run_id": "r-crawl", "tool": "zap", "profile": "baseline"},
        findings={"total": 2, "by_verdict": {"false-positive": 2}, "true_positives": 0,
                  "false_positives": 2, "duplicates": 0, "ambiguous": 0,
                  "precision": 0.0, "precision_conservative": 0.0,
                  "false_positive_list": [{"method": "GET", "url": "/x", "param": "a",
                                           "cwe": [79], "name": "XSS", "reason": "no match"}]},
    )
    return [good, crawler]


def test_tool_name_includes_version_and_profile(make_catalog):
    good, crawler = two_runs(make_catalog)
    assert tool_name(good) == "acme-ptaas 3.1 (full)"
    assert tool_name(crawler) == "zap (baseline)"


def test_markdown_has_the_headline_tables(make_catalog):
    md = markdown_report(two_runs(make_catalog))
    assert "## Overall" in md
    for edition in ("2017", "2021", "2025"):
        assert f"## OWASP Top 10 {edition} — trigger recall" in md
    assert "## By family — reach / exercise / trigger" in md
    assert "## By rendering mode — where SPA crawling collapses" in md
    assert "## Precision" in md
    # both tools appear as rows of the same tables
    assert md.count("| acme-ptaas 3.1 (full) |") >= 4
    assert md.count("| zap (baseline) |") >= 4
    # cells carry both the fraction and the raw counts
    assert "100% (1/1)" in md and "0% (0/1)" in md
    assert "R 100% · E 100% · T 100%" in md


def test_markdown_marks_empty_buckets_with_a_dash(make_catalog):
    md = markdown_report(two_runs(make_catalog))
    assert "—" in md


def test_render_table_shows_the_spa_cliff(make_catalog):
    md = markdown_report(two_runs(make_catalog))
    render_block = md.split("## By rendering mode")[1]
    assert "static→SPA reach delta" in render_block
    assert "-100 pts" in render_block  # zap reached static-html only


def test_html_is_self_contained_and_theme_aware(make_catalog):
    page = html_report(two_runs(make_catalog))
    assert page.startswith("<!doctype html>")
    assert "<script" not in page.lower()
    assert "prefers-color-scheme" in page
    # No external resource of any kind: the dashboard must open from a USB stick.
    for marker in ("http://", "https://", "//cdn", "<link", "@import", "src="):
        assert marker not in page.replace("&#x2F;", ""), marker
    assert "acme-ptaas 3.1 (full)" in page and "zap (baseline)" in page
    assert "False positives — zap (baseline)" in page


def test_html_escapes_hostile_content(make_catalog):
    docs = two_runs(make_catalog)
    docs[1]["run"]["tool"] = "<img src=x onerror=alert(1)>"
    page = html_report(docs)
    assert "<img src=x" not in page
    assert "&lt;img src=x" in page


def test_write_report_emits_three_artifacts(tmp_path, make_catalog):
    docs = two_runs(make_catalog)
    written = write_report(docs, tmp_path / "out")
    assert [p.name for p in written] == ["results.md", "results.html", "results.json"]
    assert all(p.exists() and p.stat().st_size > 0 for p in written)
    raw = json.loads((tmp_path / "out" / "results.json").read_text())
    assert len(raw["runs"]) == 2
    assert load_score(tmp_path / "out" / "results.json")["runs"][0]["run"]["tool"] == "acme-ptaas"


def test_report_scales_to_n_tools(make_catalog):
    docs = two_runs(make_catalog)
    many = []
    for i in range(6):
        clone = json.loads(json.dumps(docs[i % 2]))
        clone["run"]["tool"] = f"tool{i}"
        clone["run"]["tool_version"] = None
        clone["run"]["profile"] = None
        many.append(clone)
    md = markdown_report(many)
    for i in range(6):
        assert f"| tool{i} |" in md


def with_new_blocks(make_catalog):
    """A run carrying a crawl block, a weak attribution and inventory-aware FPs."""
    docs = two_runs(make_catalog)
    good, crawler = docs
    good["metrics"]["crawl"] = {
        "inventory_available": True, "apps": ["shopfront"],
        "surface": {"routes": 8, "covered": 7, "coverage": 0.875},
        "planted_routes": {"routes": 2, "covered": 2, "coverage": 1.0},
        "safe_routes": {"routes": 6, "covered": 5, "coverage": 5 / 6},
        "by_app": {"shopfront": {"routes": 8, "covered": 7, "coverage": 0.875}},
        "by_render": {"static-html": {"routes": 4, "covered": 4, "coverage": 1.0},
                      "spa-react": {"routes": 4, "covered": 3, "coverage": 0.75}},
        "by_auth": {}, "planted_vuln_reach": {"hit": 2, "applicable": 2, "recall": 1.0},
        "requests_off_inventory": 0, "unvisited_routes": [],
    }
    crawler["metrics"]["crawl"] = {
        "inventory_available": True, "apps": ["shopfront"],
        "surface": {"routes": 8, "covered": 2, "coverage": 0.25},
        "planted_routes": {"routes": 2, "covered": 1, "coverage": 0.5},
        "safe_routes": {"routes": 6, "covered": 1, "coverage": 1 / 6},
        "by_app": {"shopfront": {"routes": 8, "covered": 2, "coverage": 0.25}},
        "by_render": {"static-html": {"routes": 4, "covered": 2, "coverage": 0.5},
                      "spa-react": {"routes": 4, "covered": 0, "coverage": 0.0}},
        "by_auth": {}, "planted_vuln_reach": {"hit": 1, "applicable": 2, "recall": 0.5},
        "requests_off_inventory": 3, "unvisited_routes": [],
    }
    crawler["low_confidence_triggers"] = {
        "count": 1, "vuln_ids": ["BENCH-SHOP-0002"],
        "credited_only_here": ["BENCH-SHOP-0002"],
        "headline_trigger": {"hit": 0, "applicable": 2, "recall": 0.0},
        "inclusive_trigger": {"hit": 1, "applicable": 2, "recall": 0.5},
        "attributions": [{"vuln_id": "BENCH-SHOP-0002", "kind": "container-window",
                          "confidence": "low", "channel": "dns"}],
        "unattributed_callbacks": [{"vuln_id": None, "kind": "unattributed",
                                    "confidence": "low", "channel": "dns"}],
    }
    crawler["metrics"]["overall"]["trigger_any"] = {"hit": 1, "applicable": 2, "recall": 0.5}
    crawler["metrics"]["by_family"]["xss"] = {
        "vulns": 1,
        "reach": {"hit": 0, "applicable": 1, "recall": 0.0},
        "exercise": {"hit": 0, "applicable": 1, "recall": 0.0},
        "trigger": {"hit": 0, "applicable": 1, "recall": 0.0},
        "trigger_any": {"hit": 1, "applicable": 1, "recall": 1.0},
    }
    crawler["findings"].update({"false_positives_confirmed": 1,
                                "false_positives_unknown_route": 1,
                                "precision_confirmed": 0.0,
                                "inventory_available": True})
    crawler["findings"]["false_positive_list"][0]["fp_basis"] = "inventory-safe-route"
    return docs


def test_crawl_section_shows_both_denominators(make_catalog):
    md = markdown_report(with_new_blocks(make_catalog))
    assert "## Crawl coverage — whole published surface" in md
    block = md.split("## Crawl coverage")[1].split("##")[0]
    assert "whole surface" in block and "planted-vuln reach" in block
    assert "25% (2/8)" in block          # crawler walked a quarter of the surface
    assert "50% (1/2)" in block          # ...while planted-only reach flatters it
    assert "surface: spa-react" in block


def test_weak_attribution_section_is_separate_from_the_headline(make_catalog):
    docs = with_new_blocks(make_catalog)
    md = markdown_report(docs)
    assert "## Out-of-band attribution strength" in md
    block = md.split("## Out-of-band attribution strength")[1].split("##")[0]
    assert "headline trigger" in block and "incl. weak attribution" in block
    assert "BENCH-SHOP-0002" in block
    # the family table flags it as a suffix, never folded into T
    family_block = md.split("## By family")[1].split("##")[0]
    assert "(+1 weak)" in family_block


def test_summary_table_carries_the_new_columns(make_catalog):
    md = markdown_report(with_new_blocks(make_catalog))
    summary = md.split("## Overall")[1].split("##")[0]
    assert "trigger +weak oob" in summary
    assert "surface crawl" in summary
    # The headline precision is the confirmed one, and findings outside the corpus
    # get their own column rather than being folded into false positives.
    assert "precision (confirmed)" in summary
    assert "FP confirmed" in summary
    assert "outside corpus" in summary


def test_precision_table_shows_the_confirmed_reading(make_catalog):
    md = markdown_report(with_new_blocks(make_catalog))
    block = md.split("## Precision")[1].split("##")[0]
    assert "FP confirmed" in block and "FP unconfirmable" in block


def test_html_renders_the_new_sections_offline(make_catalog):
    page = html_report(with_new_blocks(make_catalog))
    assert "Crawl coverage" in page and "Out-of-band attribution strength" in page
    assert "inventory-safe-route" in page
    assert "<script" not in page.lower()
    for marker in ("http://", "https://", "<link", "@import"):
        assert marker not in page, marker


def test_sections_are_skipped_when_the_data_is_absent(make_catalog):
    md = markdown_report(two_runs(make_catalog))
    assert "## Crawl coverage" not in md
    assert "## Out-of-band attribution strength" not in md
