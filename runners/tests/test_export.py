"""Exporting the event stream, and keeping other applications out of it.

The events ARE the ground truth: reach, exercise and trigger all derive from them.
The scorer cannot fetch them itself -- the collector answers only to its own loopback,
which is the property that keeps the answer key away from the tool -- so a run
directory without them cannot be re-scored by anyone who does not have the collector
in front of them, which contradicts the promise the whole record makes.
"""

from __future__ import annotations

import json

from runners._lib.collector import CollectorClient
from runners._lib.config import AppSpec
from runners._lib.internal_http import Response
from runners._lib.topology import address_owner, inspect_apps

from fakes import FakeDocker, FakeHttp, container_inspect

BASE = "http://127.0.0.1:8900"


def paged(pages: list[list[dict]]):
    """A collector that answers the paged export, one page per call."""
    state = {"i": 0}

    def handler(method, url, json_body, headers):
        i = state["i"]
        state["i"] += 1
        if i >= len(pages):
            return Response(200, json.dumps({"events": [], "next_seq": None}))
        events = pages[i]
        return Response(
            200,
            json.dumps({"events": events, "next_seq": events[-1]["seq"] if events else None}),
        )

    return handler


def event(seq: int, **kw) -> dict:
    base = {"seq": seq, "type": "http_request", "app": "blog", "synthetic": False}
    base.update(kw)
    return base


def client(pages: list[list[dict]]) -> CollectorClient:
    http = FakeHttp()
    http.routes = {}
    http.default = paged(pages)
    return CollectorClient(http, BASE)


def test_the_whole_paged_stream_lands_in_the_run_directory(tmp_path):
    pages = [[event(i) for i in range(1, 5001)], [event(i) for i in range(5001, 5100)]]
    summary = client(pages).export_events("r1", tmp_path / "events.jsonl")
    lines = (tmp_path / "events.jsonl").read_text().strip().splitlines()
    assert len(lines) == 5099 == summary["events"]
    assert json.loads(lines[0])["seq"] == 1
    assert summary["by_type"] == {"http_request": 5099}


def test_the_export_counts_requests_per_application(tmp_path):
    """The figure the log line reports, in the record, attributable to a target."""
    pages = [[
        event(1, app="blog"),
        event(2, app="blog"),
        event(3, app="blog", synthetic=True),
        event(4, app="blog", type="trigger"),
    ]]
    summary = client(pages).export_events("r1", tmp_path / "events.jsonl")
    # Synthetic traffic is the platform's own and is not the tool's coverage; a
    # trigger is not a request.
    assert summary["requests_by_app"] == {"blog": 2}
    assert summary["by_app"] == {"blog": 4}


def test_a_truncated_export_is_reported_rather_than_silently_short(tmp_path, caplog):
    http = FakeHttp()
    http.default = Response(500, "boom")
    summary = CollectorClient(http, BASE).export_events("r1", tmp_path / "events.jsonl")
    assert summary["events"] == 0
    assert "cannot be re-scored" in caplog.text


# -- scope --------------------------------------------------------------------------


def scope_filter(apps: list[str], topologies) -> object:
    """The orchestrator's filter, reproduced here so the rule itself is tested."""
    from runners.orchestrate import Orchestrator

    orchestrator = Orchestrator.__new__(Orchestrator)
    orchestrator.apps = [AppSpec(key=a, services=[a], base_url=f"http://{a}") for a in apps]
    orchestrator.topology = topologies
    return orchestrator._scope_filter()


def test_another_applications_events_are_kept_out_of_the_record(tmp_path):
    """The resolver is shared, so a target that is not in scope can emit a DNS lookup
    while a run is open. It scores as an orphan callback rather than crediting
    anyone, but a published run must not contain it as though the tool caused it."""
    keep = scope_filter(["blog"], {})
    pages = [[
        event(1, app="blog"),
        event(2, app="admin"),
        event(3, app="blog", type="trigger"),
    ]]
    summary = client(pages).export_events(
        "r1",
        tmp_path / "events.jsonl",
        in_scope=keep,
        out_of_scope_path=tmp_path / "out.jsonl",
    )
    assert summary["events"] == 2 and summary["out_of_scope"] == 1
    assert json.loads((tmp_path / "out.jsonl").read_text().strip())["app"] == "admin"


def test_an_out_of_band_callback_is_attributed_by_source_address():
    """An oob event carries no `app`: the sinkhole sees an address and nothing else,
    which is exactly what the address map is for."""
    docker = FakeDocker(containers={
        "cid-admin": container_inspect("admin", addresses={"bench-public": "10.88.0.15"}),
    })
    topologies = inspect_apps(docker, [AppSpec(key="admin", services=["admin"], base_url="http://a")])
    assert address_owner(topologies) == {"10.88.0.15": "admin"}

    keep = scope_filter(["blog"], topologies)
    assert not keep({"type": "oob", "source_ip": "10.88.0.15", "token": "hooks.example"})


def test_an_unattributable_callback_is_kept():
    """It may be the tool's own callback, which is the single most valuable event in
    the stream -- dropping it would delete the proof of a blind vulnerability."""
    keep = scope_filter(["blog"], {})
    assert keep({"type": "oob", "source_ip": "10.88.0.99", "token": "x"})


def test_an_event_with_neither_app_nor_address_is_kept():
    keep = scope_filter(["blog"], {})
    assert keep({"type": "note", "message": "..."})


# -- how a run must not be misread --------------------------------------------------


def test_a_passive_run_says_so_where_a_reader_will_see_it(tmp_path):
    """ZAP's baseline profile is passive: it sends no attack traffic, so its zero
    exploitation is structural and says nothing about ZAP. A passive run's document
    and an active run's document must not look alike."""
    from test_drivers import SHOP, make_ctx

    from runners.zap import ZapDriver

    driver = ZapDriver()
    baseline = make_ctx(tmp_path / "b", "zap", "baseline", [SHOP])
    full = make_ctx(tmp_path / "f", "zap", "full", [SHOP])
    assert driver.performs_active_scanning(baseline, driver.plan(baseline))[0] is False
    assert driver.performs_active_scanning(full, driver.plan(full))[0] is True


def test_caveats_name_every_reason_a_zero_is_not_a_miss(tmp_path):
    from runners._lib.budget import StopReason
    from runners.orchestrate import Orchestrator, build_parser

    args = build_parser().parse_args(
        ["--tool", "zap", "--profile", "baseline", "--app", "blog", "--results-dir", str(tmp_path)]
    )
    orchestrator = Orchestrator(args)
    orchestrator.scan_mode = {"active": False, "reason": "baseline profile", "mode": "passive-only"}
    orchestrator.browser = {
        "declared": False, "reason": "no browser", "catalog_entries_requiring_js": {"blog": 4},
    }

    class _Findings:
        findings = list(range(21))

    caveats = orchestrator._caveats({
        "stop_reason": StopReason.BUDGET_WALL_CLOCK.value,
        "normalised": _Findings(),
        "unscoreable_findings": 10,
        "sessions": {},
        "preparation": [],
    })
    joined = " ".join(caveats)
    assert "PASSIVE ONLY" in joined
    assert "NO BROWSER" in joined and "4 catalog" in joined
    assert "BUDGET EXHAUSTED" in joined
    # The fairness bug that published 0.0% precision for a scanner that reported
    # nothing false: findings for a class the corpus does not plant are unscoreable.
    assert "unscoreable, not false positives" in joined


def test_an_out_of_catalog_cwe_is_counted_separately_from_a_false_positive():
    """CWE-693 ("CSP header not set") is a real finding for a class this corpus
    deliberately does not plant. It can never match ground truth, so counting it as a
    false positive publishes a precision the tool did not earn."""
    from runners._lib.normalise import default_table

    table = default_table()
    assert 693 in table.out_of_catalog
    assert "no catalog class plants it" in table.out_of_catalog[693]
