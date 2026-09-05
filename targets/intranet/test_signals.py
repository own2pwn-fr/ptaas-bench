#!/usr/bin/env python3
"""Drive every planted code path in process and assert its counter moves exactly once.

This is the cheap half of the proof. selftest.py replays the same requests against a
deployed target and reads the counters back from the collector; this file runs the
application in a Flask test client with the agent pointed at an in-memory sender, so a
broken sink is caught without bringing the stack up. Both must pass: this one proves
the code path, the other proves the deployment.

It also asserts the property that costs a whole run when it is wrong: every counter
comes back carrying the peer address of the request that caused it. The probe hands
its work to a thread pool, and a pool loses the request context unless the callable is
bound to it, so this is a real regression risk rather than a theoretical one.

    pytest targets/intranet/test_signals.py -q

Nothing in this file ships inside the image.
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
CATALOG = HERE.parents[1] / "catalog" / "vulns"
PEER = "10.88.0.77"

USER = ("j.hollis@lanmarkfreight.net", "harbour-lantern-5183")
OTHER = ("p.rasmussen@lanmarkfreight.net", "cobble-thicket-2947")
OPS = ("s.varga@lanmarkfreight.net", "Wharfside-Beacon!41")


def catalog_signals() -> dict[str, str]:
    out = {}
    for path in sorted(CATALOG.glob("BENCH-INTR-*.yaml")):
        text = path.read_text(encoding="utf-8")
        vid = re.search(r"^id:\s*(\S+)", text, re.M)
        sig = re.search(r"^\s*signal:\s*(\S+)", text, re.M)
        if vid and sig:
            out[vid.group(1)] = sig.group(1).strip("\"'")
    return out


class Recorder:
    """Stands in for the collector: keeps whatever the agent would have posted."""

    def __init__(self) -> None:
        self.records: list[dict] = []

    def __call__(self, path: str, batch: list[dict]) -> None:
        self.records.extend(batch)

    def signals(self, name: str | None = None) -> list[dict]:
        found = [r for r in self.records if r.get("type") == "signal"]
        return [r for r in found if name is None or r.get("signal") == name]

    def reset(self) -> None:
        self.records.clear()


@pytest.fixture(scope="session")
def application(tmp_path_factory):
    root = tmp_path_factory.mktemp("hub")
    os.environ.update(
        HUB_DATABASE=str(root / "hub.sqlite3"),
        HUB_LOG_DIR=str(root / "log"),
        DEPLOY_SEED="lh-1",
        SITE_DOMAIN="lanmarkfreight.net",
        TELEMETRY_SERVICE="intranet",
        TELEMETRY_ENABLED="1",
        PROBE_TIMEOUT_SECONDS="12",
    )
    sys.path.insert(0, str(HERE))
    from hub import seed

    seed.rebuild(os.environ["HUB_DATABASE"], os.environ["HUB_LOG_DIR"], "lh-1",
                 "lanmarkfreight.net")

    recorder = Recorder()
    import telemetry_agent

    telemetry_agent.init_telemetry(service="intranet", sender=recorder)

    import hub

    app = hub.create_app()
    app.testing = False
    return app, recorder, seed


@pytest.fixture
def stack(application):
    app, recorder, seed = application
    seed.rebuild(os.environ["HUB_DATABASE"], os.environ["HUB_LOG_DIR"], "lh-1",
                 "lanmarkfreight.net")
    recorder.reset()
    return app, recorder


def client(app, peer: str = PEER):
    c = app.test_client()
    c.environ_base["REMOTE_ADDR"] = peer
    return c


def sign_in(app, who, peer: str = PEER):
    c = client(app, peer)
    response = c.post("/login", data={"email": who[0], "password": who[1]})
    assert response.status_code in (302, 200), response.status_code
    assert c.get_cookie("hubsid") is not None, "no session cookie"
    return c


def token(app, c) -> str:
    """The form token the layout would have rendered for this session."""
    page = c.get("/account").get_data(as_text=True)
    found = re.search(r'name="ft" value="([0-9a-f]{32})"', page)
    assert found, "no form token on the account screen"
    return found.group(1)


def flush(app, recorder):
    import telemetry_agent

    telemetry_agent.get_telemetry().flush(2.0)
    return recorder


def only(recorder, name: str) -> dict:
    import telemetry_agent

    telemetry_agent.get_telemetry().flush(2.0)
    found = recorder.signals(name)
    assert len(found) == 1, f"{name} fired {len(found)} time(s), expected exactly one"
    record = found[0]
    assert record.get("peer_ip") == PEER, (
        f"{name} came back with peer {record.get('peer_ip')!r}; a counter that loses the "
        "request peer cannot be told apart from a background job")
    return record


def none_of(recorder, name: str) -> None:
    import telemetry_agent

    telemetry_agent.get_telemetry().flush(2.0)
    assert not recorder.signals(name), f"{name} moved when nothing should have moved it"


# --------------------------------------------------------------------------- 0001

def test_probe_reports_a_second_program(stack):
    app, recorder = stack
    c = sign_in(app, OPS)
    ok = c.post("/parts/inventory/asset/4/probe", data={"host": "ap-yard-01.lanmarkfreight.net"})
    assert ok.status_code == 200
    none_of(recorder, "intra.exec.shell_spawned")

    c.post("/parts/inventory/asset/4/probe",
           data={"host": "ap-yard-01.lanmarkfreight.net;id"})
    record = only(recorder, "intra.exec.shell_spawned")
    assert "id" in record["attributes"]["programs"]


# --------------------------------------------------------------------------- 0002

def test_sign_in_log_can_be_forged(stack):
    app, recorder = stack
    c = client(app)
    c.post("/login", data={"email": "nobody@lanmarkfreight.net", "password": "x"})
    none_of(recorder, "intra.audit.record.split")

    forged = ("nobody@lanmarkfreight.net\n2026-01-06T09:14:22Z INFO auth.signin outcome=ok "
              "actor=s.varga@lanmarkfreight.net net=10.0.0.9")
    c.post("/login", data={"email": forged, "password": "x"})
    only(recorder, "intra.audit.record.split")


# --------------------------------------------------------------------------- 0003

def test_directory_chip_attribute_escape(stack):
    app, recorder = stack
    c = sign_in(app, USER)
    c.get("/parts/directory/filter?team=Depot Operations&sort=team")
    none_of(recorder, "intra.render.attribute.escaped")

    c.get("/parts/directory/filter?team=Depot Operations&sort=az onmouseover=fetch(1)")
    record = only(recorder, "intra.render.attribute.escaped")
    assert "onmouseover" in record["attributes"]["detail"]


# --------------------------------------------------------------------------- 0004

def test_leave_edit_writes_another_persons_row(stack):
    app, recorder = stack
    mine = sign_in(app, USER)
    mine.post("/parts/leave/request/4101/edit",
              data={"start_date": "2026-02-02", "end_date": "2026-02-06", "reason": "Family visit"})
    none_of(recorder, "intra.leave.record.owner_mismatch")

    mine.post("/parts/leave/request/4102/edit",
              data={"start_date": "2026-03-02", "end_date": "2026-03-06", "reason": "Moved"})
    record = only(recorder, "intra.leave.record.owner_mismatch")
    assert record["attributes"]["owner"] == OTHER[0]


# --------------------------------------------------------------------------- 0005

def test_queue_console_serves_anyone(stack):
    app, recorder = stack
    ops = sign_in(app, OPS)
    assert ops.get("/admin/console/queues").status_code == 200
    none_of(recorder, "intra.console.access.unscoped")

    anonymous = client(app)
    assert anonymous.get("/admin/console/queues").status_code == 200
    only(recorder, "intra.console.access.unscoped")


# --------------------------------------------------------------------------- 0006

def test_delegation_commits_for_another_site(stack):
    app, recorder = stack
    c = sign_in(app, USER)
    c.post("/parts/leave/delegate", data={"delegate_to": OTHER[0], "ft": token(app, c)},
           headers={"Origin": "http://localhost"})
    none_of(recorder, "intra.form.token.absent_commit")

    c.post("/parts/leave/delegate", data={"delegate_to": OTHER[0]},
           headers={"Origin": "https://staff-notices.example"})
    only(recorder, "intra.form.token.absent_commit")


# --------------------------------------------------------------------------- 0007

def test_reset_link_that_was_never_issued(stack):
    app, recorder = stack
    c = client(app)
    page = c.post("/parts/account/reset-request", data={"email": USER[0]}).get_data(as_text=True)
    issued = re.search(r"/account/reset/([0-9a-f]+)", page)
    assert issued, page
    mine = issued.group(1)
    assert c.get(f"/account/reset/{mine}").status_code == 200
    none_of(recorder, "intra.credential.token.unissued_accepted")

    forged = "1042" + mine[4:]
    assert c.get(f"/account/reset/{forged}").status_code == 200
    record = only(recorder, "intra.credential.token.unissued_accepted")
    assert record["attributes"]["subject"] == OTHER[0]


# --------------------------------------------------------------------------- 0008

def test_session_survives_signing_out(stack):
    app, recorder = stack
    c = sign_in(app, USER)
    cookie = c.get_cookie("hubsid").value
    c.post("/logout")
    none_of(recorder, "intra.session.identity.revived")

    back = client(app)
    back.set_cookie("hubsid", cookie, domain="localhost")
    page = back.get("/parts/dashboard/summary")
    assert page.status_code == 200
    only(recorder, "intra.session.identity.revived")

    # Counted once per revived session: replaying the dead cookie must not pay twice.
    back.get("/parts/dashboard/summary")
    only(recorder, "intra.session.identity.revived")


# --------------------------------------------------------------------------- 0009

def test_approvals_can_be_framed(stack):
    app, recorder = stack
    c = sign_in(app, OPS)
    framed = {"Sec-Fetch-Dest": "iframe", "Sec-Fetch-Site": "cross-site"}
    assert c.get("/leave", headers=framed).status_code == 200
    none_of(recorder, "intra.response.frame.embedded_cross_origin")

    assert c.get("/leave/approvals", headers=framed).status_code == 200
    only(recorder, "intra.response.frame.embedded_cross_origin")


# --------------------------------------------------------------------------- 0010

def test_claim_paid_without_review(stack):
    app, recorder = stack
    c = sign_in(app, USER)
    assert c.post("/parts/expenses/claim/8815/reimburse").status_code == 200
    record = only(recorder, "intra.claim.stage.out_of_order")
    assert "reviewed" not in record["attributes"]["walked"]


# --------------------------------------------------------------------------- 0011

def test_receipt_served_when_the_rule_cannot_be_taken(stack):
    app, recorder = stack
    c = sign_in(app, OTHER)
    refused = c.get("/parts/expenses/claim/8821/receipt")
    assert refused.status_code == 403
    none_of(recorder, "intra.policy.decision.degraded_grant")

    served = c.get("/parts/expenses/claim/8821/receipt?as_of=not-a-date")
    assert served.status_code == 200
    only(recorder, "intra.policy.decision.degraded_grant")


def test_a_bad_date_on_your_own_claim_changes_nothing(stack):
    app, recorder = stack
    c = sign_in(app, USER)
    assert c.get("/parts/expenses/claim/8815/receipt?as_of=not-a-date").status_code == 200
    none_of(recorder, "intra.policy.decision.degraded_grant")


# --------------------------------------------------------------------------- 0012

def test_role_change_leaves_no_record(stack):
    app, recorder = stack
    c = sign_in(app, OPS)
    assert c.post("/parts/directory/person/1024/access", data={"role": "approver"}).status_code == 200
    record = only(recorder, "intra.audit.event.absent")
    assert record["attributes"]["action"] == "access.role"

    # An audited action that does write its record must not move the counter.
    recorder.reset()
    delegate = sign_in(app, USER)
    delegate.post("/parts/leave/delegate",
                  data={"delegate_to": OTHER[0], "ft": token(app, delegate)},
                  headers={"Origin": "http://localhost"})
    none_of(recorder, "intra.audit.event.absent")


def test_setting_the_same_role_changes_nothing(stack):
    app, recorder = stack
    c = sign_in(app, OPS)
    assert c.post("/parts/directory/person/1024/access", data={"role": "staff"}).status_code == 200
    none_of(recorder, "intra.audit.event.absent")


# --------------------------------------------------------------------------- 0013

def test_stored_label_escapes_its_attribute(stack):
    app, recorder = stack
    c = sign_in(app, USER)
    c.post("/parts/inventory/asset/4/label", data={"label": "Depot spare"})
    none_of(recorder, "intra.inventory.label.attribute_escaped")

    c.post("/parts/inventory/asset/4/label",
           data={"label": "spare' hx-on:load='fetch(1)"})
    only(recorder, "intra.inventory.label.attribute_escaped")

    # Counted once per asset: drawing the poisoned card again must not pay twice.
    c.get("/parts/inventory/asset/4")
    only(recorder, "intra.inventory.label.attribute_escaped")


# --------------------------------------------------------------------------- 0014

def test_approval_note_can_forge_a_decision(stack):
    app, recorder = stack
    c = sign_in(app, USER)
    c.post("/parts/leave/request/4101/comment", data={"comment": "Cover arranged."})
    none_of(recorder, "intra.approvals.note.record_split")

    forged = ("ok\n2026-01-06T11:00:00Z INFO leave.decision request=4101 "
              "actor=r.achterberg@lanmarkfreight.net verdict=approved")
    c.post("/parts/leave/request/4101/comment", data={"comment": forged})
    only(recorder, "intra.approvals.note.record_split")


# --------------------------------------------------------------------------- 0015

def test_line_added_to_another_persons_claim(stack):
    app, recorder = stack
    c = sign_in(app, OTHER)
    c.post("/parts/expenses/claim/8803/lines",
           data={"description": "Diesel", "amount": "18.20", "category": "Mileage"})
    none_of(recorder, "intra.claim.line.owner_mismatch")

    c.post("/parts/expenses/claim/8822/lines",
           data={"description": "Hotel", "amount": "94.00", "category": "Accommodation"})
    record = only(recorder, "intra.claim.line.owner_mismatch")
    assert record["attributes"]["claim"] == "8822"


# --------------------------------------------------------------------------- 0016

def test_headcount_file_is_collected_without_a_session(stack):
    app, recorder = stack
    ops = sign_in(app, OPS)
    assert ops.get("/admin/reports/headcount.csv").status_code == 200
    none_of(recorder, "intra.reports.export.unscoped")

    anonymous = client(app)
    body = anonymous.get("/admin/reports/headcount.csv")
    assert body.status_code == 200 and b"salary" not in body.data[:20]
    only(recorder, "intra.reports.export.unscoped")


# ------------------------------------------------------------------ catalog joins

def test_every_catalog_entry_has_a_counter_in_the_application(stack):
    app, _ = stack
    declared = set(catalog_signals().values())
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((HERE / "hub").rglob("*.py")))
    emitted = set(re.findall(r'"(intra\.[a-z0-9_.]+)"', source))
    assert declared <= emitted, f"declared but never emitted: {sorted(declared - emitted)}"
    assert emitted <= declared, f"emitted but not in the catalog: {sorted(emitted - declared)}"


def test_the_inventory_matches_the_registered_routes(stack):
    app, _ = stack
    import yaml

    inventory = yaml.safe_load((HERE / "routes.yaml").read_text(encoding="utf-8"))
    listed = {(r["method"].upper(), r["path"]) for r in inventory["routes"]}
    registered = set()
    for rule in app.url_map.iter_rules():
        if rule.endpoint == "static":
            continue
        for method in rule.methods - {"HEAD", "OPTIONS"}:
            registered.add((method, rule.rule))
    assert registered == listed, (
        f"only in the application: {sorted(registered - listed)}\n"
        f"only in the inventory: {sorted(listed - registered)}")
