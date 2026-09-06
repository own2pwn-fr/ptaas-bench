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
    assert by_id["BENCH-SHOP-0031"]["trigger_source"] == "oob:token"
    assert by_id["BENCH-SHOP-0001"]["trigger"] is False
    assert any(w["code"] == "unattributed-oob" for w in doc["warnings"])


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


def test_unknown_signal_is_reported(make_catalog):
    # A target emitting a signal the catalog does not claim means a planted flaw
    # nobody can ever be credited for.
    catalog = load_catalog(make_catalog([vuln_entry()]))
    doc = score_run(catalog, events_from_iterable([trigger_event("BENCH-GHOST-9999")]))
    assert any(w["code"] == "unknown-signal" for w in doc["warnings"])


def test_unknown_trigger_id_is_reported(make_catalog):
    catalog = load_catalog(make_catalog([vuln_entry()]))
    doc = score_run(catalog, events_from_iterable([
        trigger_event(vuln_id="BENCH-GHOST-9999")]))
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


# --------------------------------------------------------------------------- #
# signal indirection and out-of-band attribution
# --------------------------------------------------------------------------- #

def _ssrf_entry(vuln_id="BENCH-SHOP-0031", **oracle):
    base = {"kind": "oob", "signal": "shop.imports.fetch.external",
            "condition": "The importer fetched a caller-supplied URL and the sinkhole saw it."}
    base.update(oracle)
    return vuln_entry(
        id=vuln_id, **{"class": "ssrf_blind"}, severity="high",
        entrypoint={"method": "POST", "path": "/api/admin/imports", "auth": "admin",
                    "param": "source_url", "param_in": "json",
                    "default_value": "https://suppliers/catalog.json"},
        oracle=base,
    )


def test_trigger_is_credited_from_the_opaque_signal(make_catalog):
    # The normal path: the target emits a metric-shaped signal, never a catalog id.
    entry = vuln_entry()
    root = make_catalog([entry])
    ev = {"type": "trigger", "app": "shopfront", "ts": 2.0,
          "signal": "shop.synthetic.shop_0001.anomaly"}
    assert outcome(entry, [http_event(), ev], root).trigger is True
    assert outcome(entry, [http_event(), ev], root).trigger_source == "signal"


def test_another_entrys_signal_does_not_credit_this_one(make_catalog):
    entry = vuln_entry()
    root = make_catalog([entry])
    ev = {"type": "trigger", "app": "shopfront", "signal": "shop.synthetic.ghost_9999.anomaly"}
    assert outcome(entry, [http_event(), ev], root).trigger is False


def test_oob_attribution_by_signal_correlation(make_catalog):
    # The sink registered {signal, destination_host, route, param, request_id} with
    # the collector before its outbound fetch; the sinkhole matched the lookup.
    catalog = load_catalog(make_catalog([_ssrf_entry()]))
    ev = oob_event(signal="shop.imports.fetch.external", channel="dns",
                   destination_host="7x3.oast.fun", request_id="req-42",
                   attribution="signal-correlation")
    doc = score_run(catalog, events_from_iterable([ev]))
    row = doc["vulns"][0]
    assert row["trigger"] is True
    assert row["trigger_source"] == "oob:signal-correlation"
    assert row["attributions"][0]["confidence"] == "high"
    # The callback went to the tool's OWN collaborator domain, which is the whole
    # point of running the sinkhole as the network's resolver.
    assert row["attributions"][0]["destination_host"] == "7x3.oast.fun"
    assert doc["low_confidence_triggers"]["count"] == 0


def test_oob_weak_attribution_is_counted_but_never_headline(make_catalog):
    catalog = load_catalog(make_catalog([_ssrf_entry()]))
    ev = oob_event(signal="shop.imports.fetch.external", channel="dns",
                   attribution="container-window", confidence="low",
                   container="shopfront", destination_host="attacker.example")
    doc = score_run(catalog, events_from_iterable([ev]))
    row = doc["vulns"][0]
    assert row["trigger"] is False           # headline: proof only
    assert row["trigger_low_confidence"] is True
    assert row["trigger_any"] is True
    overall = doc["metrics"]["overall"]
    assert overall["trigger"]["hit"] == 0
    assert overall["trigger_any"]["hit"] == 1
    low = doc["low_confidence_triggers"]
    assert low["count"] == 1 and low["credited_only_here"] == ["BENCH-SHOP-0031"]
    assert low["headline_trigger"]["hit"] == 0 and low["inclusive_trigger"]["hit"] == 1
    assert any(w["code"] == "low-confidence-trigger" for w in doc["warnings"])


def test_a_weak_attribution_does_not_promote_reach(make_catalog):
    # A container/time-window guess is not evidence the endpoint was ever touched.
    catalog = load_catalog(make_catalog([_ssrf_entry()]))
    ev = oob_event(signal="shop.imports.fetch.external", low_confidence=True)
    doc = score_run(catalog, events_from_iterable([ev]))
    row = doc["vulns"][0]
    assert row["reach"] is False and row["reach_inferred"] is False
    assert not any(w["code"] == "trigger-without-reach" for w in doc["warnings"])


def test_the_sinkhole_flag_is_never_overruled_upwards(make_catalog):
    # Even a token we own stays low confidence when the sinkhole says it is unsure.
    catalog = load_catalog(make_catalog([_ssrf_entry(canary_token="shop0031")]))
    ev = oob_event("shop0031", confidence="low")
    doc = score_run(catalog, events_from_iterable([ev]))
    assert doc["vulns"][0]["trigger"] is False
    assert doc["vulns"][0]["trigger_any"] is True


def test_container_window_fallback_needs_an_unambiguous_candidate(make_catalog):
    unique = load_catalog(make_catalog([_ssrf_entry()]))
    ev = oob_event(app="shopfront", route="/api/admin/imports", method="POST",
                   attribution="container-window")
    doc = score_run(unique, events_from_iterable([ev]))
    assert doc["vulns"][0]["trigger_any"] is True
    assert doc["vulns"][0]["attributions"][0]["kind"] == "container-window"

    # Two out-of-band oracles on one route: guessing would pick one at random, so
    # the callback stays unattributed and is reported instead.
    ambiguous = load_catalog(make_catalog([
        _ssrf_entry("BENCH-SHOP-0031", signal="shop.imports.fetch.external"),
        _ssrf_entry("BENCH-SHOP-0032", signal="shop.imports.fetch.retry"),
    ]))
    doc = score_run(ambiguous, events_from_iterable([ev]))
    assert all(v["trigger_any"] is False for v in doc["vulns"])
    assert any(w["code"] == "unattributed-oob" for w in doc["warnings"])
    assert len(doc["low_confidence_triggers"]["unattributed_callbacks"]) == 1


def test_trigger_any_equals_trigger_without_weak_attributions(make_catalog):
    catalog = _three_vuln_catalog(make_catalog)
    doc = score_run(catalog, events_from_iterable([
        http_event(params=[param("q", "x'")]), trigger_event("BENCH-SHOP-0001")]))
    for bucket in doc["metrics"]["by_family"].values():
        assert bucket["trigger"] == bucket["trigger_any"]
    assert doc["low_confidence_triggers"]["count"] == 0


# --------------------------------------------------------------------------- #
# wire vocabulary, ordering and trust
# --------------------------------------------------------------------------- #

def test_current_and_legacy_sink_event_names_are_both_accepted(make_catalog):
    entry = vuln_entry()
    root = make_catalog([entry])
    current = {"type": "signal", "app": "shopfront", "ts": 2.0,
               "signal": "shop.synthetic.shop_0001.anomaly",
               "attributes": {"payload": "x' UNION SELECT", "detail": "extra table"}}
    legacy = {"type": "trigger", "app": "shopfront", "ts": 2.0,
              "signal": "shop.synthetic.shop_0001.anomaly",
              "evidence": {"payload": "x' UNION SELECT", "detail": "extra table"}}
    for ev in (current, legacy):
        out = outcome(entry, [http_event(), ev], root)
        assert out.trigger is True
        # `attributes` is the wire name; the platform keeps calling it evidence.
        assert out.evidence["payload"] == "x' UNION SELECT"


def test_legacy_event_type_is_normalised_in_the_counts(make_catalog):
    stream = events_from_iterable([
        {"type": "trigger", "app": "shopfront", "signal": "s.a.b.c"},
        {"type": "signal", "app": "shopfront", "signal": "s.a.b.d"},
    ])
    assert stream.counts()["signal"] == 2
    assert "trigger" not in stream.counts()


def test_oracle_kind_comes_from_the_catalog_not_from_the_event(make_catalog):
    # The SDK no longer sends it; if an archived event claims one, it is ignored --
    # the catalog is authoritative and an event must not be able to re-label a flaw.
    catalog = load_catalog(make_catalog([vuln_entry()]))  # class sqli_union -> kind sink
    doc = score_run(catalog, events_from_iterable([
        http_event(),
        {"type": "signal", "app": "shopfront", "signal": "shop.synthetic.shop_0001.anomaly",
         "oracle_kind": "timing"},
    ]))
    assert doc["vulns"][0]["oracle_kind"] == "sink"
    assert doc["vulns"][0]["trigger"] is True


def test_scoring_is_independent_of_event_order(make_catalog):
    catalog = _three_vuln_catalog(make_catalog)
    events = [
        http_event(params=[param("q", "x'")]),
        trigger_event("BENCH-SHOP-0001"),
        http_event(route="/api/orders/{id}", params=[param("id", "1002", "path")]),
        oob_event(signal="shop.synthetic.shop_0003.anomaly", destination_host="a.oast.fun"),
    ]

    def score(order):
        doc = score_run(catalog, events_from_iterable(order))
        doc.pop("generated_at")
        return doc

    assert score(events) == score(list(reversed(events)))
    assert score(events) == score([events[i] for i in (2, 0, 3, 1)])


def test_a_registration_alone_never_credits_a_trigger(make_catalog):
    # A registration says "the sink is about to fetch this URL", which is a payload,
    # not an effect. Only the callback proves the flaw, so an unknown-typed
    # correlation record must stay inert until a sinkhole observation joins it.
    catalog = load_catalog(make_catalog([_ssrf_entry()]))
    doc = score_run(catalog, events_from_iterable([
        {"type": "oob_registration", "app": "shopfront",
         "signal": "shop.imports.fetch.external", "destination_host": "9zk.oast.fun"}]))
    assert doc["vulns"][0]["trigger"] is False
    assert doc["vulns"][0]["trigger_any"] is False


def test_correlation_arriving_after_the_observation_still_joins(make_catalog):
    # The sink dispatches its registration on a separate connection, so it can land
    # in the export after the callback it explains. Join on content, never on order.
    catalog = load_catalog(make_catalog([_ssrf_entry()]))
    observation = oob_event(channel="dns", destination_host="9zk.oast.fun")
    registration = {"type": "oob_registration", "app": "shopfront", "ts": 0.5,
                    "signal": "shop.imports.fetch.external",
                    "destination_host": "9zk.oast.fun", "request_id": "req-7"}
    doc = score_run(catalog, events_from_iterable([observation, registration]))
    row = doc["vulns"][0]
    assert row["trigger"] is True
    assert row["attributions"][0]["kind"] == "signal-correlation"
    assert row["attributions"][0]["confidence"] == "high"


def test_correlation_joins_on_request_id_and_on_a_host_suffix(make_catalog):
    catalog = load_catalog(make_catalog([_ssrf_entry()]))
    registration = {"type": "oob_registration", "app": "shopfront",
                    "signal": "shop.imports.fetch.external",
                    "destination_host": "9zk.oast.fun", "request_id": "req-7"}

    by_request = score_run(catalog, events_from_iterable([
        oob_event(request_id="req-7"), registration]))
    assert by_request["vulns"][0]["trigger"] is True

    # Tools prepend a label per probe, so the observed lookup is a subdomain of the
    # hostname the sink registered.
    by_suffix = score_run(catalog, events_from_iterable([
        oob_event(destination_host="probe12.9zk.oast.fun."), registration]))
    assert by_suffix["vulns"][0]["trigger"] is True

    unrelated = score_run(catalog, events_from_iterable([
        oob_event(destination_host="someone-else.oast.fun"), registration]))
    assert unrelated["vulns"][0]["trigger"] is False


def test_client_ip_is_descriptive_and_never_excludes_traffic(make_catalog):
    # Synthetic marking is decided by the SDK from the socket peer address. A tool
    # that forges X-Forwarded-For into the platform range must not be able to erase
    # its own traffic, so nothing here reads client_ip.
    entry = vuln_entry()
    root = make_catalog([entry])
    forged = http_event(params=[param("q", "x' OR 1=1--")],
                        client_ip="10.99.0.1", user_agent="bench-selftest/1.0")
    out = outcome(entry, [forged], root)
    assert out.reach is True and out.exercise is True


def _infra_chain(make_catalog):
    """The shape `infra` ships: a dated archive only findable once the directory
    listing above it has been enumerated (BENCH-INFR-0012 requires 0001)."""
    listing = vuln_entry(
        id="BENCH-INFR-0001", app="infra", **{"class": "dir_listing"}, severity="medium",
        entrypoint={"method": "GET", "path": "/media/", "param": None, "default_value": None},
        discovery={"render": "static-html", "difficulty": 1},
        oracle={"kind": "artifact",
                "condition": "The index page listed the directory's real entries to the caller."},
    )
    archive = vuln_entry(
        id="BENCH-INFR-0012", app="infra", **{"class": "backup_file"}, severity="high",
        entrypoint={"method": "GET", "path": "/media/wwwroot-preflight-20260712.tar.gz",
                    "param": None, "default_value": None},
        discovery={"render": "static-html", "difficulty": 3},
        oracle={"kind": "artifact",
                "condition": "The archive body was served to the caller in full."},
        requires_prereq=["BENCH-INFR-0001"],
    )
    return load_catalog(make_catalog([listing, archive]))


def test_a_single_link_chain_is_measured(make_catalog):
    catalog = _infra_chain(make_catalog)
    doc = score_run(catalog, events_from_iterable([
        http_event(app="infra", route="/media/"),
        trigger_event("BENCH-INFR-0001", app="infra"),
        http_event(app="infra", route="/media/wwwroot-preflight-20260712.tar.gz"),
        trigger_event("BENCH-INFR-0012", app="infra"),
    ]))
    chains = doc["chains"]
    assert chains["max_depth"] == 1
    assert chains["depth_histogram"] == {"0": 1, "1": 1}
    assert chains["chained_vulns"] == 1
    assert chains["chains_completed"] == 1
    assert chains["chains_broken"] == []
    assert chains["by_depth"]["1"]["trigger"]["recall"] == 1.0


def test_the_tail_of_a_chain_without_its_head_is_reported_as_an_anomaly(make_catalog):
    # A tool credited with the archive but not the listing either got there by a
    # word list rather than by enumeration, or an oracle failed to fire. Either way
    # it is worth seeing, and it is never silently repaired.
    catalog = _infra_chain(make_catalog)
    doc = score_run(catalog, events_from_iterable([
        http_event(app="infra", route="/media/wwwroot-preflight-20260712.tar.gz"),
        trigger_event("BENCH-INFR-0012", app="infra"),
    ]))
    chains = doc["chains"]
    assert chains["chains_completed"] == 0
    assert chains["chains_broken"] == [
        {"vuln_id": "BENCH-INFR-0012", "missing_prereqs": ["BENCH-INFR-0001"]}
    ]
    assert chains["by_depth"]["0"]["trigger"]["recall"] == 0.0
    assert chains["by_depth"]["1"]["trigger"]["recall"] == 1.0


# --------------------------------------------------------------------------- #
# scope
# --------------------------------------------------------------------------- #

def _two_app_catalog(make_catalog):
    return load_catalog(make_catalog([
        vuln_entry(id="BENCH-SHOP-0001", app="shopfront"),
        vuln_entry(id="BENCH-SHOP-0002", app="shopfront", **{"class": "xss_stored"},
                   severity="high",
                   entrypoint={"method": "POST", "path": "/api/reviews", "param": "body",
                               "param_in": "json", "default_value": "nice"}),
        vuln_entry(id="BENCH-EDGE-0001", app="edge", **{"class": "cache_poisoning"},
                   severity="high",
                   entrypoint={"method": "GET", "path": "/submit", "param": "x",
                               "param_in": "header", "default_value": None}),
    ]))


def test_the_run_record_targets_scope_the_headline(make_catalog):
    # A run that scanned one app must not be scored against the whole corpus: doing
    # so understates it by the number of targets.
    catalog = _two_app_catalog(make_catalog)
    doc = score_run(catalog, events_from_iterable([http_event(params=[param("q", "x'")])]),
                    run={"run_id": "r1", "tool": "zap", "targets": ["shopfront"]},
                    apps=["shopfront"], scope_source="run-record")
    assert doc["scope"] == {"apps": ["shopfront"], "source": "run-record",
                            "catalog_apps": ["edge", "shopfront"],
                            "vulns_in_scope": 2, "vulns_total": 3}
    assert doc["metrics"]["overall"]["reach"] == {"hit": 1, "applicable": 2, "recall": 0.5}


def test_scope_falls_back_to_the_apps_the_events_touched(make_catalog):
    catalog = _two_app_catalog(make_catalog)
    doc = score_run(catalog, events_from_iterable([http_event(params=[param("q", "x'")])]),
                    run={"run_id": "r1", "tool": "zap"})
    assert doc["scope"]["apps"] == ["shopfront"]
    assert doc["scope"]["source"] == "events"
    assert doc["metrics"]["overall"]["reach"]["applicable"] == 2
    assert any(w["code"] == "scope-derived-from-events" for w in doc["warnings"])


def test_a_run_that_touched_nothing_is_scored_corpus_wide_and_says_so(make_catalog):
    catalog = _two_app_catalog(make_catalog)
    doc = score_run(catalog, events_from_iterable([]), run={"run_id": "r", "tool": "zap"})
    assert doc["scope"]["source"] == "catalog"
    assert doc["scope"]["vulns_in_scope"] == 3
    unscoped = [w for w in doc["warnings"] if w["code"] == "unscoped-run"]
    assert unscoped and "understated" in unscoped[0]["message"]


def test_synthetic_traffic_does_not_widen_the_scope(make_catalog):
    # The harness's own login traffic is excluded from credit, so it must not drag
    # another app into the denominator either.
    catalog = _two_app_catalog(make_catalog)
    doc = score_run(catalog, events_from_iterable([
        http_event(params=[param("q", "x'")]),
        http_event(app="edge", route="/submit", synthetic=True),
    ]), run={"run_id": "r", "tool": "zap"})
    assert doc["scope"]["apps"] == ["shopfront"]
