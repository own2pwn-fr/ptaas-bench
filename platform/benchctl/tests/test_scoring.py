"""Reach / exercise / trigger semantics and aggregation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from benchctl.catalog import load_catalog
from benchctl.events import events_from_iterable, load_events
from benchctl.scoring import param_in_matches, score_run, score_vuln, sha256_of
from conftest import REPO_ROOT, http_event, oob_event, param, trigger_event, vuln_entry

SCORE_SCHEMA = REPO_ROOT / "results" / "schema" / "score.schema.json"


def outcome(entry, events, catalog_root):
    catalog = load_catalog(catalog_root)
    stream = events_from_iterable(events).scored()
    return score_vuln(catalog.by_id[entry["id"]], stream)


# --------------------------------------------------------------------------- #
# REACH
# --------------------------------------------------------------------------- #

def test_reach_needs_app_method_and_route(make_catalog):
    entry = vuln_entry(entrypoint={"path": "/api/orders/:id", "method": "GET",
                                   "param": "id", "param_in": "path"})
    root = make_catalog([entry])
    assert outcome(entry, [http_event(route="/api/orders/{id}")], root).reach
    assert not outcome(entry, [http_event(route="/api/orders/{id}", app="other")], root).reach
    assert not outcome(entry, [http_event(route="/api/orders/{id}", method="POST")], root).reach
    assert not outcome(entry, [http_event(route="/api/invoices/{id}")], root).reach


def test_head_does_not_credit_reach_on_a_get(make_catalog):
    entry = vuln_entry()
    root = make_catalog([entry])
    # A HEAD cannot observe the flaw, so it must not count as having reached it.
    assert not outcome(entry, [http_event(method="HEAD")], root).reach


def test_unmatched_route_never_credits_reach(make_catalog):
    entry = vuln_entry(entrypoint={"path": "/api/orders/:id"})
    root = make_catalog([entry])
    events = [http_event(route="<unmatched>", path="/api/orders/1002")]
    assert not outcome(entry, events, root).reach


def test_reach_counts_every_matching_request(make_catalog):
    entry = vuln_entry()
    root = make_catalog([entry])
    out = outcome(entry, [http_event(ts=5.0), http_event(ts=3.0), http_event(ts=9.0)], root)
    assert out.reach_events == 3
    assert out.first_reach_ts == 3.0


# --------------------------------------------------------------------------- #
# EXERCISE
# --------------------------------------------------------------------------- #

def test_exercise_requires_a_value_differing_from_the_default(make_catalog):
    entry = vuln_entry()  # default_value "laptop"
    root = make_catalog([entry])
    replayed = [http_event(params=[param("q", "laptop")])]
    fuzzed = [http_event(params=[param("q", "laptop' UNION SELECT 1--")])]
    assert outcome(entry, replayed, root).exercise is False  # a crawler
    assert outcome(entry, fuzzed, root).exercise is True     # a scanner


def test_exercise_uses_the_hash_not_the_sample(make_catalog):
    entry = vuln_entry()
    root = make_catalog([entry])
    # Only the hash is authoritative: here the sample is truncated but the hash of
    # the full value already proves the tool sent something else.
    ev = http_event(params=[{"name": "q", "in": "query",
                             "value_sha256": sha256_of("x" * 900), "value_len": 900,
                             "sample": "x" * 256}])
    assert outcome(entry, [ev], root).exercise is True


def test_exercise_falls_back_to_an_untruncated_sample(make_catalog):
    entry = vuln_entry()
    root = make_catalog([entry])
    ev = http_event(params=[{"name": "q", "in": "query", "value_len": 5, "sample": "1 or 1"[:5]}])
    assert outcome(entry, [ev], root).exercise is True


def test_null_default_value_accepts_any_non_empty_value(make_catalog):
    entry = vuln_entry(entrypoint={"default_value": None})
    root = make_catalog([entry])
    assert outcome(entry, [http_event(params=[param("q", "anything")])], root).exercise is True
    assert outcome(entry, [http_event(params=[param("q", "")])], root).exercise is False


def test_null_param_makes_exercise_not_applicable(make_catalog):
    entry = vuln_entry(
        **{"class": "exposed_vcs"}, severity="high",
        entrypoint={"path": "/.git/config", "param": None, "default_value": None},
        oracle={"kind": "artifact", "condition": "The repository object store was read by the caller."},
    )
    root = make_catalog([entry])
    out = outcome(entry, [http_event(route="/.git/config")], root)
    # null, never False: a param-less vulnerability must not dilute the average.
    assert out.exercise is None
    assert out.reach is True


def test_not_applicable_exercise_is_excluded_from_denominators(make_catalog):
    entries = [
        vuln_entry(id="BENCH-SHOP-0001"),
        vuln_entry(id="BENCH-SHOP-0002", **{"class": "exposed_vcs"}, severity="high",
                   entrypoint={"path": "/.git/config", "param": None, "default_value": None},
                   oracle={"kind": "artifact", "condition": "The repository object store was read."}),
    ]
    root = make_catalog(entries)
    catalog = load_catalog(root)
    events = [http_event(params=[param("q", "payload")]), http_event(route="/.git/config")]
    doc = score_run(catalog, events_from_iterable(events))
    assert doc["metrics"]["overall"]["reach"] == {"hit": 2, "applicable": 2, "recall": 1.0}
    assert doc["metrics"]["overall"]["exercise"] == {"hit": 1, "applicable": 1, "recall": 1.0}


def test_param_location_must_match_except_for_body_dialects(make_catalog):
    entry = vuln_entry(entrypoint={"method": "POST", "param": "source_url",
                                   "param_in": "json", "default_value": "https://x/y"})
    root = make_catalog([entry])
    as_body = [http_event(method="POST", params=[param("source_url", "http://oob/", "body")])]
    as_query = [http_event(method="POST", params=[param("source_url", "http://oob/", "query")])]
    assert outcome(entry, as_body, root).exercise is True
    assert outcome(entry, as_query, root).exercise is False
    assert param_in_matches("json", "multipart")
    assert not param_in_matches("query", "json")
    assert not param_in_matches("path", "query")


def test_a_different_parameter_does_not_credit_exercise(make_catalog):
    entry = vuln_entry()
    root = make_catalog([entry])
    ev = http_event(params=[param("sort", "'; DROP TABLE users--")])
    assert outcome(entry, [ev], root).exercise is False


# --------------------------------------------------------------------------- #
# TRIGGER
# --------------------------------------------------------------------------- #

def test_trigger_event_credits_only_its_own_vuln(make_catalog):
    entry = vuln_entry()
    root = make_catalog([entry])
    assert outcome(entry, [http_event(), trigger_event("BENCH-SHOP-0001")], root).trigger
    assert not outcome(entry, [http_event(), trigger_event("BENCH-SHOP-0002")], root).trigger


def test_oob_token_maps_to_the_right_vulnerability(make_catalog):
    entries = [
        vuln_entry(id="BENCH-SHOP-0001"),
        vuln_entry(id="BENCH-SHOP-0031", **{"class": "ssrf_blind"}, severity="high",
                   entrypoint={"method": "POST", "path": "/api/admin/imports",
                               "param": "source_url", "param_in": "json",
                               "default_value": "https://suppliers/catalog.json"},
                   oracle={"kind": "oob", "canary_token": "shop0031",
                           "condition": "The canary received a callback carrying this token."}),
    ]
    root = make_catalog(entries)
    catalog = load_catalog(root)
    stream = events_from_iterable([
        http_event(method="POST", route="/api/admin/imports",
                   params=[param("source_url", "http://shop0031.oob/x", "json")]),
        oob_event("shop0031"),
        oob_event("someone-elses-token"),
    ])
    doc = score_run(catalog, stream)
    by_id = {v["id"]: v for v in doc["vulns"]}
    assert by_id["BENCH-SHOP-0031"]["trigger"] is True
    assert by_id["BENCH-SHOP-0031"]["trigger_source"] == "oob"
    assert by_id["BENCH-SHOP-0001"]["trigger"] is False
    assert any(w["code"] == "unknown-oob-token" for w in doc["warnings"])


def test_trigger_implies_reach_and_warns(make_catalog):
    entry = vuln_entry()
    root = make_catalog([entry])
    catalog = load_catalog(root)
    doc = score_run(catalog, events_from_iterable([trigger_event("BENCH-SHOP-0001")]))
    row = doc["vulns"][0]
    assert row["reach"] is True and row["reach_inferred"] is True
    assert doc["metrics"]["overall"]["reach"]["hit"] == 1
    assert {w["code"] for w in doc["warnings"]} >= {"trigger-without-reach", "trigger-without-exercise"}


def test_trigger_never_promotes_exercise(make_catalog):
    entry = vuln_entry()
    root = make_catalog([entry])
    catalog = load_catalog(root)
    doc = score_run(catalog, events_from_iterable([http_event(), trigger_event("BENCH-SHOP-0001")]))
    row = doc["vulns"][0]
    assert row["trigger"] is True
    assert row["exercise"] is False  # an oracle firing is not evidence of fuzzing


def test_unknown_trigger_id_is_reported(make_catalog):
    catalog = load_catalog(make_catalog([vuln_entry()]))
    doc = score_run(catalog, events_from_iterable([trigger_event("BENCH-GHOST-9999")]))
    assert any(w["code"] == "unknown-trigger-id" for w in doc["warnings"])


# --------------------------------------------------------------------------- #
# synthetic exclusion
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("synthetic", [True, "true"])
def test_synthetic_events_are_excluded_everywhere(make_catalog, synthetic):
    entries = [vuln_entry(id="BENCH-SHOP-0001",
                          oracle={"kind": "oob", "canary_token": "tok",
                                  "condition": "The canary received a callback carrying this token."})]
    catalog = load_catalog(make_catalog(entries))
    events = [
        http_event(params=[param("q", "payload")], synthetic=synthetic),
        trigger_event("BENCH-SHOP-0001", synthetic=synthetic),
        oob_event("tok", synthetic=synthetic),
    ]
    doc = score_run(catalog, events_from_iterable(events))
    row = doc["vulns"][0]
    assert (row["reach"], row["exercise"], row["trigger"]) == (False, False, False)
    # ...but they are still counted in the raw event tally, so a reader can see
    # that the platform's own traffic was present and was discarded.
    assert doc["events"]["synthetic"] == 3


# --------------------------------------------------------------------------- #
# aggregation
# --------------------------------------------------------------------------- #

def _three_vuln_catalog(make_catalog):
    entries = [
        vuln_entry(id="BENCH-SHOP-0001"),
        vuln_entry(id="BENCH-SHOP-0002", **{"class": "bola"}, severity="critical",
                   entrypoint={"method": "GET", "path": "/api/orders/:id", "auth": "other-user",
                               "param": "id", "param_in": "path", "default_value": "1001"},
                   discovery={"render": "spa-react", "requires": ["js-execution"], "difficulty": 3}),
        vuln_entry(id="BENCH-SHOP-0003", **{"class": "xss_stored"}, severity="high",
                   entrypoint={"method": "POST", "path": "/api/reviews", "param": "body",
                               "param_in": "json", "default_value": "nice"},
                   discovery={"render": "spa-react", "requires": ["js-execution", "form-submit"],
                              "difficulty": 4},
                   requires_prereq=["BENCH-SHOP-0002"]),
    ]
    return load_catalog(make_catalog(entries))


def test_breakdowns_cover_every_required_axis(make_catalog):
    catalog = _three_vuln_catalog(make_catalog)
    events = [
        http_event(params=[param("q", "' OR 1=1--")]),
        trigger_event("BENCH-SHOP-0001"),
        http_event(route="/api/orders/{id}", params=[param("id", "1002", "path")]),
    ]
    doc = score_run(catalog, events_from_iterable(events))
    m = doc["metrics"]
    assert set(m) >= {"overall", "by_app", "by_owasp", "by_family", "by_class", "by_severity",
                      "by_render", "by_difficulty", "by_auth", "by_requires"}
    assert set(m["by_owasp"]) == {"2017", "2021", "2025"}
    assert m["by_owasp"]["2021"]["A03"]["trigger"]["hit"] == 1
    assert m["by_family"]["access-control"]["reach"]["recall"] == 1.0
    assert m["by_render"]["static-html"]["trigger"]["recall"] == 1.0
    assert m["by_render"]["spa-react"]["trigger"]["recall"] == 0.0
    assert m["by_auth"]["other-user"]["exercise"]["recall"] == 1.0
    assert m["by_difficulty"]["3"]["reach"]["hit"] == 1
    # requires buckets overlap on purpose: one vuln can need several capabilities.
    assert m["by_requires"]["js-execution"]["vulns"] == 2
    assert m["by_requires"]["form-submit"]["vulns"] == 2
    assert m["by_class"]["xss_stored"]["reach"]["recall"] == 0.0


def test_empty_bucket_recall_is_null_not_zero(make_catalog):
    catalog = _three_vuln_catalog(make_catalog)
    doc = score_run(catalog, events_from_iterable([]))
    assert doc["metrics"]["by_owasp"]["2021"]["A03"]["trigger"]["recall"] == 0.0
    assert "A05" not in doc["metrics"]["by_owasp"]["2021"]  # no ground truth there at all


def test_chain_statistics(make_catalog):
    catalog = _three_vuln_catalog(make_catalog)
    events = [trigger_event("BENCH-SHOP-0002"), trigger_event("BENCH-SHOP-0003")]
    doc = score_run(catalog, events_from_iterable(events))
    assert doc["chains"]["max_depth"] == 1
    assert doc["chains"]["chained_vulns"] == 1
    assert doc["chains"]["chains_completed"] == 1
    assert doc["chains"]["chains_broken"] == []
    assert doc["chains"]["by_depth"]["1"]["trigger"]["recall"] == 1.0


def test_broken_chain_is_surfaced(make_catalog):
    catalog = _three_vuln_catalog(make_catalog)
    doc = score_run(catalog, events_from_iterable([trigger_event("BENCH-SHOP-0003")]))
    assert doc["chains"]["chains_completed"] == 0
    assert doc["chains"]["chains_broken"] == [
        {"vuln_id": "BENCH-SHOP-0003", "missing_prereqs": ["BENCH-SHOP-0002"]}
    ]


def test_apps_scope_restricts_denominators(make_catalog):
    entries = [
        vuln_entry(id="BENCH-SHOP-0001", app="shopfront"),
        vuln_entry(id="BENCH-BANK-0001", app="bank"),
    ]
    catalog = load_catalog(make_catalog(entries))
    doc = score_run(catalog, events_from_iterable([]), apps=["shopfront"])
    assert doc["catalog"]["vulns_in_scope"] == 1
    assert doc["metrics"]["overall"]["reach"]["applicable"] == 1


def test_score_document_matches_its_published_schema(make_catalog):
    catalog = _three_vuln_catalog(make_catalog)
    events = [http_event(params=[param("q", "x'")]), trigger_event("BENCH-SHOP-0001"),
              oob_event("nobody")]
    doc = score_run(catalog, events_from_iterable(events), run={"run_id": "r1", "tool": "zap"})
    validator = Draft202012Validator(json.loads(SCORE_SCHEMA.read_text()))
    assert sorted(e.message for e in validator.iter_errors(doc)) == []
    # Round-trips through JSON: the document is an archive format.
    assert json.loads(json.dumps(doc)) == doc


def test_events_file_shapes(tmp_path):
    payload = {"run": {"run_id": "r1", "tool": "zap"}, "events": [http_event()]}
    p = tmp_path / "events.json"
    p.write_text(json.dumps(payload))
    stream, meta = load_events(p)
    assert meta["tool"] == "zap" and len(stream.events) == 1

    bare = tmp_path / "bare.json"
    bare.write_text(json.dumps([http_event(), trigger_event("BENCH-SHOP-0001")]))
    stream, meta = load_events(bare)
    assert meta == {} and stream.counts()["total"] == 2


def test_fetch_events_follows_the_cursor(monkeypatch):
    """The collector paginates; a non-advancing cursor must not hang scoring."""
    from benchctl import events as events_mod

    pages = {
        0: {"events": [http_event(), http_event()], "next_seq": 2},
        2: {"events": [trigger_event("BENCH-SHOP-0001")], "next_seq": 3},
        3: {"events": [], "next_seq": 3},
    }
    seen: list[str] = []

    def fake_get(url, timeout):
        seen.append(url)
        if url.endswith("/v1/runs"):
            return [{"run_id": "r1", "tool": "zap"}]
        after = 0
        if "after_seq=" in url:
            after = int(url.split("after_seq=")[1].split("&")[0])
        return pages[after]

    monkeypatch.setattr(events_mod, "_get_json", fake_get)
    stream, meta = events_mod.fetch_events("http://collector:8900/", "r1")
    assert stream.counts()["total"] == 3
    assert meta["tool"] == "zap"
    assert sum(1 for u in seen if "/events" in u) == 3
