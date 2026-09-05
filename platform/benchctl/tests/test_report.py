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
