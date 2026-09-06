"""End-to-end scoring of a real harness run, from the artefacts it actually writes.

Everything here is deliberately real: ``tests/data/`` holds the ``run.json``,
``findings.json`` and a route-deduplicated slice of the ``events.jsonl`` produced by
the first active run (ZAP full against ``blog``, 2026-09-06). Hand-built fixtures
agree with the reader by construction, which is exactly how this boundary produced
three defects in one day -- an attribution object read as a string, a JSONL export
parsed as a JSON document, and a container map looked for under key names nobody
writes. Each was two components agreeing on a contract in prose and disagreeing in
fact, and each would have been caught by scoring one real run.

The committed fixtures are the CI guard (``results/runs/`` is not committed); the
last test additionally scores every real run present in the working tree.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from benchctl.catalog import load_catalog
from benchctl.cli import main
from benchctl.events import (
    address_index,
    host_app_index,
    load_events,
    normalize_run_record,
)
from benchctl.inventory import load_inventories
from benchctl.report import is_passive, markdown_report
from benchctl.scoring import score_run
from conftest import REPO_ROOT

DATA = Path(__file__).parent / "data"
RUN_RECORD = DATA / "run-record-zap-blog.json"
EVENTS = DATA / "events-zap-blog.jsonl"
FINDINGS = DATA / "findings-zap-blog.json"


@pytest.fixture
def record() -> dict:
    return json.loads(RUN_RECORD.read_text(encoding="utf-8"))


def test_the_container_map_is_read_from_what_the_harness_writes(record):
    # The harness writes `addresses` (and `target_topology`), one entry per app with
    # the containers nested under `services`. Looking for `containers` instead is how
    # a run that carried the map still raised `missing-container-map`.
    normalised = normalize_run_record(record)
    assert normalised["container_map_available"] is True
    blog = normalised["containers"]["blog"]
    # Addresses are flattened from every service on every network it is attached to.
    assert set(blog["addresses"]) >= {"10.77.0.7", "10.88.0.2"}
    assert len(blog["services"]) >= 3
    index = address_index(normalised["containers"])
    assert index["10.88.0.2"] == index["10.77.0.7"] == "blog"


def test_target_images_and_the_tool_image_are_kept_apart(record):
    normalised = normalize_run_record(record)
    assert normalised["images"]["blog"].startswith("platform-edge-blog-web@sha256:")
    # The tool's own image is provenance too, but it is not a target image.
    assert normalised["tool_image_digest"].startswith("ghcr.io/zaproxy/zaproxy@sha256:")
    assert "zaproxy" not in json.dumps(normalised["images"])


def test_the_reset_digest_list_is_understood(record):
    # The harness writes a list of per-app results comparing the digest it read
    # against the seeded reference, not a before/after pair.
    normalised = normalize_run_record(record)
    blog = normalised["reset_digests"]["blog"]
    assert blog["match"] is True and blog["ok"] is True
    assert normalised["reset_consistent"] is True


def test_scan_mode_caveats_and_request_counts_are_read(record):
    normalised = normalize_run_record(record)
    assert normalised["scan_mode"] == {"mode": "active", "active": True,
                                       "reason": "activeScan job present"}
    assert normalised["caveats"] and "unscoreable" in normalised["caveats"][0]
    assert normalised["requests"]["total"] == 14328
    assert normalised["requests"]["by_app"]["blog"] == 14215


def test_the_jsonl_export_is_read_as_json_lines():
    stream, _ = load_events(EVENTS)
    assert len(stream.events) > 10
    assert stream.counts()["http_request"] > 10
    # The sink that fired during the run is in the slice, with its wire spelling.
    signals = [e for e in stream.triggers if e.signal]
    assert signals and signals[0].signal == "blog.embed.card.template_escape"
    assert signals[0].evidence["payload"].startswith("zj{{")


def test_findings_resolve_through_the_host_headers_the_collector_saw():
    # ZAP reports against `press01:8000`, a hostname generated per deployment that
    # appears in no catalog or inventory. The collector saw which app served it.
    stream, _ = load_events(EVENTS)
    index = host_app_index(stream.scored())
    assert index["press01"] == "blog"
    # The wire carries the bare host; a finding carries host:port. The lookup tries
    # the authority and then the host, so both resolve.
    from benchctl.findings import _resolve_app, finding_from_dict

    f = finding_from_dict({"tool": "zap", "url": "http://press01:8000/api/embed/card?title=x"})
    assert _resolve_app(f, index) == "blog"


def _score_real_run(tmp_path: Path) -> dict:
    run_dir = tmp_path / "24e8683856e04100971860e406acfe41"
    run_dir.mkdir()
    shutil.copy(RUN_RECORD, run_dir / "run.json")
    shutil.copy(EVENTS, run_dir / "events.jsonl")
    shutil.copy(FINDINGS, run_dir / "findings.json")
    assert main(["--root", str(REPO_ROOT), "score", "--run", str(run_dir)]) == 0
    return json.loads((run_dir / "score.json").read_text())


def test_scoring_a_run_directory_discovers_its_artefacts(tmp_path):
    doc = _score_real_run(tmp_path)
    assert doc["run"]["tool"] == "zap"
    assert doc["run"]["profile"] == "full"
    assert doc["scope"] == {"apps": ["blog"], "source": "run-record",
                            "catalog_apps": doc["scope"]["catalog_apps"],
                            "vulns_in_scope": doc["scope"]["vulns_in_scope"],
                            "vulns_total": doc["scope"]["vulns_total"]}
    assert doc["scope"]["source"] == "run-record"
    # Everything the record carries is now read, so neither complaint stands.
    codes = {w["code"] for w in doc["warnings"]}
    assert "missing-container-map" not in codes
    assert "incomplete-run-record" not in codes
    assert "unscoped-run" not in codes


def test_a_run_directory_gets_its_report_written_beside_the_evidence(tmp_path):
    _score_real_run(tmp_path)
    run_dir = tmp_path / "24e8683856e04100971860e406acfe41"
    assert (run_dir / "score.json").is_file()
    assert (run_dir / "results.md").is_file()
    assert (run_dir / "results.html").is_file()


def test_the_scanner_is_credited_for_what_it_actually_found(tmp_path):
    doc = _score_real_run(tmp_path)
    f = doc["findings"]
    matched = {r["matched_vuln"] for r in f["findings"] if r["verdict"] == "true-positive"}
    # ZAP did report the template injection whose oracle fired during the run.
    assert "BENCH-BLOG-0006" in matched
    assert f["true_positives"] >= 1
    assert f["precision_confirmed"] is not None and f["precision_confirmed"] > 0
    # ...and the header-hygiene alerts are still set aside rather than counted wrong.
    assert f["out_of_catalog"] >= 10


def test_the_conditions_of_the_run_are_stated_before_its_numbers(tmp_path):
    doc = _score_real_run(tmp_path)
    assert doc["run"]["scan_mode"]["active"] is True
    assert doc["run"]["caveats"]
    assert doc["run"]["requests"]["total"] == 14328
    md = markdown_report([doc])
    banner = md.split("## Overall")[0]
    assert "active run" in banner
    assert "unscoreable" in banner          # the harness's own caveat
    assert "14,328 requests observed" in banner
    assert is_passive(doc) is False


def test_a_passive_run_says_its_exploitation_score_is_structural(tmp_path):
    # The failure this guards against: reading a passive baseline's trigger 0% as a
    # capability result, which is only avoidable if the page says so itself.
    doc = _score_real_run(tmp_path)
    doc["run"]["scan_mode"] = {"mode": "passive", "active": False,
                               "reason": "no activeScan job"}
    catalog = load_catalog(REPO_ROOT)
    stream, meta = load_events(EVENTS)
    meta = json.loads(RUN_RECORD.read_text())
    meta["scan_mode"] = {"mode": "passive", "active": False, "reason": "no activeScan job"}
    passive = score_run(catalog, stream, run=meta, apps=["blog"], scope_source="run-record",
                        inventories=load_inventories(REPO_ROOT))
    assert any(w["code"] == "passive-scan-mode" for w in passive["warnings"])
    assert is_passive(passive) is True
    banner = markdown_report([passive]).split("## Overall")[0]
    assert "PASSIVE RUN" in banner
    assert "structurally zero" in banner
    assert "Not comparable with an active run." in banner


@pytest.mark.parametrize("run_json", sorted((REPO_ROOT / "results" / "runs").glob("*/run.json"))
                         if (REPO_ROOT / "results" / "runs").is_dir() else [])
def test_every_real_run_record_in_the_tree_is_readable(run_json):
    # results/runs/ is not committed, so this is a working-tree guard rather than a
    # CI one: whatever the harness has actually written must parse here.
    record = json.loads(run_json.read_text(encoding="utf-8"))
    normalised = normalize_run_record(record)
    assert isinstance(normalised["containers"], dict)
    assert isinstance(normalised["caveats"], list)
    if record.get("addresses"):
        assert normalised["container_map_available"] is True


def test_an_informational_alert_cannot_confirm_a_false_positive(tmp_path):
    # ZAP's passive baseline reported five "Modern Web Application" notes: no CWE,
    # no vulnerability claim. Counting them as confirmed false positives published
    # that run at 0% precision for saying nothing false, which is the same injustice
    # in a smaller shape.
    from benchctl.findings import classify_findings, finding_from_dict

    catalog = load_catalog(REPO_ROOT)
    inventories = load_inventories(REPO_ROOT)
    stream, _ = load_events(EVENTS)
    notes = [finding_from_dict({"tool": "zap", "url": f"http://press01:8000{path}",
                                "method": "GET", "param": None, "cwe": None,
                                "name": "Modern Web Application", "severity": "info",
                                "confidence": "medium"})
             for path in ("/", "/account", "/privacy")]
    report = classify_findings(catalog, notes, inventories=inventories,
                               app_map=host_app_index(stream.scored()), apps=["blog"])
    assert {r["fp_basis"] for r in report["findings"]} == {"finding-without-cwe"}
    assert report["false_positives_confirmed"] == 0
    assert report["precision_confirmed"] is None


def test_a_record_that_does_not_state_its_scan_mode_says_so(tmp_path):
    catalog = load_catalog(REPO_ROOT)
    stream, _ = load_events(EVENTS)
    record = json.loads(RUN_RECORD.read_text())
    record.pop("scan_mode")
    doc = score_run(catalog, stream, run=record, apps=["blog"], scope_source="run-record")
    assert any(w["code"] == "scan-mode-unknown" for w in doc["warnings"])
    banner = markdown_report([doc]).split("## Overall")[0]
    assert "Scan mode not recorded" in banner
