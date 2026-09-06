"""Run provenance: container/address map, image digests, reset state digests.

The map exists because every target is dual-homed: a correlation hint is registered
over bench-internal and carries a 10.77.0.x address, while the callback it explains
leaves over bench-public and is observed as 10.88.0.x. Same container, two
addresses, no numeric relationship -- so source -> app must be a lookup, never an
inference, and these tests pin that.
"""

from __future__ import annotations

import json

from jsonschema import Draft202012Validator

from benchctl.catalog import load_catalog
from benchctl.events import address_index, events_from_iterable, normalize_run_record
from benchctl.findings import classify_findings, finding_from_dict
from benchctl.report import markdown_report
from benchctl.scoring import score_run
from conftest import REPO_ROOT, http_event, oob_event, param, vuln_entry

RUN = {
    "run_id": "r1",
    "tool": "acme",
    "targets": ["shopfront", "edge"],
    "containers": {
        "shopfront": {"service": "shopfront", "container_id": "c0ffee11",
                      "addresses": ["10.77.0.4", "10.88.0.9"],
                      "image_digest": "sha256:1111111111111111111111"},
        "edge": {"service": "edge", "container_id": "deadbeef",
                 "addresses": ["10.77.0.5", "10.88.0.12"],
                 "image_digest": "sha256:2222222222222222222222"},
    },
    "reset": {"shopfront": {"before": "seed-a", "after": "seed-a"},
              "edge": {"before": "seed-b", "after": "seed-b"}},
}


def ssrf_entry(vuln_id, app, signal, path="/api/admin/imports"):
    return vuln_entry(
        id=vuln_id, app=app, **{"class": "ssrf_blind"}, severity="high",
        entrypoint={"method": "POST", "path": path, "param": "source_url",
                    "param_in": "json", "default_value": "https://suppliers/x.json"},
        oracle={"kind": "oob", "signal": signal,
                "condition": "The importer fetched a caller-supplied URL."},
    )


def test_addresses_on_every_network_resolve_to_one_app():
    index = address_index(normalize_run_record(RUN)["containers"])
    assert index["10.77.0.4"] == index["10.88.0.9"] == "shopfront"
    assert index["10.77.0.5"] == index["10.88.0.12"] == "edge"
    assert index["c0ffee11"] == "shopfront"


def test_no_arithmetic_is_done_on_addresses():
    # A neighbour address in the same /24 as a known one belongs to nobody until
    # the map says otherwise: address ranges carry no meaning here.
    index = address_index(normalize_run_record(RUN)["containers"])
    assert "10.88.0.10" not in index
    assert "10.77.0.6" not in index


def test_record_normalisation_accepts_the_shapes_that_ship():
    flat = normalize_run_record({"container_map": {"shopfront": {"ips": ["10.88.0.9"]}},
                                 "image_digests": {"shopfront": "sha256:abc"},
                                 "reset_digest_before": "x", "reset_digest_after": "x"})
    assert flat["containers"]["shopfront"]["addresses"] == ["10.88.0.9"]
    assert flat["images"] == {"shopfront": "sha256:abc"}
    assert flat["reset_digests"]["*"]["match"] is True

    per_container = normalize_run_record({"containers": {"edge": {
        "addresses": [], "image": "sha256:def",
        "reset_digest_before": "b", "reset_digest_after": "c"}}})
    assert per_container["images"] == {"edge": "sha256:def"}
    assert per_container["reset_digests"]["edge"]["match"] is False
    assert per_container["reset_consistent"] is False


def test_a_missing_side_is_unknown_not_a_mismatch():
    record = normalize_run_record({"reset": {"before": "x"}})
    assert record["reset_digests"]["*"]["match"] is None
    assert record["reset_consistent"] is None


def test_callback_source_address_resolves_the_right_target(make_catalog):
    # Two targets, one blind flaw each: before the map this was unattributable.
    catalog = load_catalog(make_catalog([
        ssrf_entry("BENCH-SHOP-0031", "shopfront", "shop.imports.fetch.external"),
        ssrf_entry("BENCH-EDGE-0031", "edge", "edge.origin.fetch.external"),
    ]))
    # The callback leaves over bench-public; the correlation hint that would have
    # named the signal never arrived.
    ev = oob_event(source_ip="10.88.0.12", channel="dns", attribution="container-window",
                   destination_host="x.oast.fun")
    doc = score_run(catalog, events_from_iterable([ev]), run=RUN)
    rows = {v["id"]: v for v in doc["vulns"]}
    assert rows["BENCH-EDGE-0031"]["trigger_any"] is True
    assert rows["BENCH-SHOP-0031"]["trigger_any"] is False
    att = rows["BENCH-EDGE-0031"]["attributions"][0]
    assert att["app"] == "edge" and att["kind"] == "container-window"
    # Exact source -> app, but the time window is still a window: low confidence.
    assert att["confidence"] == "low"
    assert rows["BENCH-EDGE-0031"]["trigger"] is False


def test_without_a_map_an_address_attributes_nothing_and_it_is_said(make_catalog):
    catalog = load_catalog(make_catalog([
        ssrf_entry("BENCH-SHOP-0031", "shopfront", "shop.imports.fetch.external")]))
    ev = oob_event(source_ip="10.88.0.9", channel="dns", attribution="container-window")
    doc = score_run(catalog, events_from_iterable([ev]), run={"run_id": "r", "tool": "t"})
    assert doc["vulns"][0]["trigger_any"] is False
    codes = {w["code"] for w in doc["warnings"]}
    assert "missing-container-map" in codes
    assert "unattributed-oob" in codes
    assert doc["run"]["container_map_available"] is False


def test_a_map_from_another_run_cannot_leak_in(make_catalog):
    # The map is only true for the run that captured it, so an address absent from
    # this run's map resolves to nothing rather than to last run's tenant.
    catalog = load_catalog(make_catalog([
        ssrf_entry("BENCH-SHOP-0031", "shopfront", "shop.imports.fetch.external")]))
    ev = oob_event(source_ip="10.88.0.77", attribution="container-window")
    doc = score_run(catalog, events_from_iterable([ev]), run=RUN)
    assert doc["vulns"][0]["trigger_any"] is False
    assert any(w["code"] == "unattributed-oob" for w in doc["warnings"])


def test_provenance_travels_with_the_score(make_catalog):
    catalog = load_catalog(make_catalog([vuln_entry()]))
    doc = score_run(catalog, events_from_iterable([http_event()]), run=RUN)
    run = doc["run"]
    assert run["container_map_available"] is True
    assert run["images"]["shopfront"].startswith("sha256:")
    assert run["reset_digests"]["edge"]["match"] is True
    assert run["reset_consistent"] is True
    assert not any(w["code"] in {"missing-container-map", "incomplete-run-record",
                                 "reset-digest-mismatch"} for w in doc["warnings"])


def test_a_dirty_reset_is_flagged(make_catalog):
    catalog = load_catalog(make_catalog([vuln_entry()]))
    dirty = dict(RUN, reset={"shopfront": {"before": "seed-a", "after": "seed-a-modified"},
                             "edge": {"before": "seed-b", "after": "seed-b"}})
    doc = score_run(catalog, events_from_iterable([http_event()]), run=dirty)
    assert doc["run"]["reset_consistent"] is False
    dirty_warnings = [w for w in doc["warnings"] if w["code"] == "reset-digest-mismatch"]
    assert len(dirty_warnings) == 1
    assert "shopfront" in dirty_warnings[0]["message"]


def test_an_incomplete_record_is_called_out(make_catalog):
    catalog = load_catalog(make_catalog([vuln_entry()]))
    doc = score_run(catalog, events_from_iterable([]), run={"run_id": "r", "tool": "t"})
    incomplete = [w for w in doc["warnings"] if w["code"] == "incomplete-run-record"]
    assert incomplete and "image digests" in incomplete[0]["message"]


def test_findings_resolve_an_app_from_a_container_address(make_catalog):
    catalog = load_catalog(make_catalog([
        vuln_entry(id="BENCH-SHOP-0001", app="shopfront"),
        vuln_entry(id="BENCH-EDGE-0001", app="edge",
                   entrypoint={"path": "/api/products", "param": "q"}),
    ]))
    app_map = address_index(normalize_run_record(RUN)["containers"])
    report = classify_findings(catalog, [finding_from_dict(
        {"tool": "zap", "url": "http://10.88.0.12:8080/api/products?q=1",
         "method": "GET", "param": "q", "cwe": 89, "name": "SQLi"})], app_map=app_map)
    assert report["findings"][0]["app"] == "edge"
    assert report["findings"][0]["matched_vuln"] == "BENCH-EDGE-0001"


def test_report_shows_provenance_and_shouts_about_a_dirty_reset(make_catalog):
    catalog = load_catalog(make_catalog([vuln_entry()]))
    clean = score_run(catalog, events_from_iterable([http_event()]), run=RUN)
    dirty = score_run(catalog, events_from_iterable([http_event()]),
                      run=dict(RUN, tool="other",
                               reset={"edge": {"before": "b", "after": "different"}}))
    md = markdown_report([clean, dirty])
    block = md.split("## Run provenance")[1]
    assert "shopfront@sha256:1111111" in block
    assert "clean (2)" in block
    assert "DIRTY: edge" in block
    assert "| yes |" in block


def test_full_record_score_document_matches_the_schema(make_catalog):
    catalog = load_catalog(make_catalog([
        vuln_entry(),
        ssrf_entry("BENCH-EDGE-0031", "edge", "edge.origin.fetch.external"),
    ]))
    doc = score_run(
        catalog,
        events_from_iterable([
            http_event(params=[param("q", "x'")]),
            oob_event(source_ip="10.88.0.12", attribution="container-window"),
        ]),
        run=RUN,
    )
    schema = json.loads((REPO_ROOT / "results" / "schema" / "score.schema.json").read_text())
    assert sorted(e.message for e in Draft202012Validator(schema).iter_errors(doc)) == []


def test_source_match_false_does_not_discount_a_host_match(make_catalog):
    # The resolver reports a host match as high confidence unconditionally and
    # records address agreement separately. A target's outbound address legitimately
    # differs from the one its correlation hint came from, so treating disagreement
    # as a downgrade would publish every genuine match as second-rate.
    catalog = load_catalog(make_catalog([
        ssrf_entry("BENCH-SHOP-0031", "shopfront", "shop.imports.fetch.external")]))
    ev = oob_event(signal="shop.imports.fetch.external", channel="dns",
                   attribution="host-match", confidence="high",
                   source_match=False, source_ip="10.88.0.9")
    doc = score_run(catalog, events_from_iterable([ev]), run=RUN)
    row = doc["vulns"][0]
    assert row["trigger"] is True
    att = row["attributions"][0]
    assert att["confidence"] == "high"
    # ...and the disagreement travels as information, not as a verdict.
    assert att["source_match"] is False
    assert doc["low_confidence_triggers"]["count"] == 0
    assert not any(w["code"] == "low-confidence-trigger" for w in doc["warnings"])


def test_an_explicit_low_confidence_flag_still_demotes(make_catalog):
    catalog = load_catalog(make_catalog([
        ssrf_entry("BENCH-SHOP-0031", "shopfront", "shop.imports.fetch.external")]))
    ev = oob_event(signal="shop.imports.fetch.external", confidence="low",
                   source_match=True)
    doc = score_run(catalog, events_from_iterable([ev]), run=RUN)
    assert doc["vulns"][0]["trigger"] is False
    assert doc["vulns"][0]["attributions"][0]["source_match"] is True


def test_a_structured_attribution_object_is_read_like_a_string(make_catalog):
    # The resolver reports {"app": ..., "mode": ...}; older streams and fixtures send
    # a bare string. Both must be read: raising on one shape made every callback
    # unscoreable on the first live run, and looked like a crash rather than a
    # contract disagreement.
    catalog = load_catalog(make_catalog([
        ssrf_entry("BENCH-SHOP-0031", "shopfront", "shop.imports.fetch.external")]))
    ev = oob_event(signal="shop.imports.fetch.external", channel="dns",
                   attribution={"app": None, "mode": "unattributed"})
    doc = score_run(catalog, events_from_iterable([ev]), run=RUN)
    row = doc["vulns"][0]
    # "unattributed" is not one of the weak markers, so the signal still credits it.
    assert row["trigger"] is True
    assert row["attributions"][0]["confidence"] == "high"


def test_a_structured_attribution_can_demote_and_can_name_the_app(make_catalog):
    catalog = load_catalog(make_catalog([
        ssrf_entry("BENCH-SHOP-0031", "shopfront", "shop.imports.fetch.external"),
        ssrf_entry("BENCH-EDGE-0031", "edge", "edge.origin.fetch.external"),
    ]))
    ev = oob_event(attribution={"app": "edge", "mode": "container-window"})
    doc = score_run(catalog, events_from_iterable([ev]), run=RUN)
    rows = {v["id"]: v for v in doc["vulns"]}
    assert rows["BENCH-EDGE-0031"]["trigger_any"] is True
    assert rows["BENCH-EDGE-0031"]["trigger"] is False   # the mode is a weak marker
    assert rows["BENCH-EDGE-0031"]["attributions"][0]["app"] == "edge"
    assert rows["BENCH-SHOP-0031"]["trigger_any"] is False


def test_structured_scalars_elsewhere_do_not_raise(make_catalog):
    # Same assumption, checked on every other scalar the wire carries.
    catalog = load_catalog(make_catalog([
        ssrf_entry("BENCH-SHOP-0031", "shopfront", "shop.imports.fetch.external")]))
    stream = events_from_iterable([
        {"type": "http_request", "app": "shopfront", "method": "POST",
         "route": {"template": "/api/admin/imports"}, "host": {"name": "shopfront"},
         "params": [{"name": {"value": "source_url"}, "in": "json",
                     "value_sha256": "deadbeef", "value_len": 12}]},
        {"type": "signal", "app": "shopfront", "signal": {"name": "shop.imports.fetch.external"}},
        {"type": "oob", "app": "canary", "channel": {"value": "dns"},
         "source_ip": {"address": "10.88.0.9"},
         "destination_host": {"host": "x.oast.fun"}, "confidence": {"level": "high"}},
    ])
    doc = score_run(catalog, stream, run=RUN)
    row = doc["vulns"][0]
    assert row["reach"] is True and row["exercise"] is True and row["trigger"] is True
