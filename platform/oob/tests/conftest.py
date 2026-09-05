"""Shared fixtures: a live service on ephemeral ports, and dumb protocol clients.

The tests drive the real listeners over real sockets rather than calling handlers
directly, because most of what can break here is wire format, not logic. Privileged
ports only exist inside the container; here every listener binds port 0 and the assigned
port is read back from ``service.ports``.
"""

from __future__ import annotations

import json
import socket
import ssl
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

from edge_resolver import dnswire
from edge_resolver.config import Config
from edge_resolver.service import ResolverService

ZONE = "telemetry-edge.net"


class FakeEndpoint:
    """Stand-in for the reporting endpoint: event intake plus the correlation registry.

    Mirrors the real service's shapes -- ``POST /v1/events``, ``POST /v1/correlations``,
    ``GET /v1/correlations`` returning ``{now, ttl, count, correlations}`` with the same
    case-folded, either-direction subdomain matching -- so a test that passes here is
    testing the contract and not a private convention.
    """

    def __init__(self, status: int = 202, control_status: int = 200) -> None:
        self.events: list[dict] = []
        self.requests = 0
        self.hint_queries: list[str | None] = []
        self.control_status = control_status
        self._hints: list[dict] = []
        self._lock = threading.Lock()
        endpoint = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def _json(self, code: int, payload: dict) -> None:
                body = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(length) or b"{}")
                path = urlparse(self.path).path
                if path == "/v1/events":
                    with endpoint._lock:
                        endpoint.requests += 1
                        endpoint.events.extend(payload.get("events", []))
                    self._json(status, {"accepted": len(payload.get("events", []))})
                    return
                if path == "/v1/correlations":
                    entry = endpoint.register(**payload)
                    self._json(202, {"registered": True, "correlation": entry})
                    return
                self._json(404, {"error": "not found"})

            def do_GET(self):  # noqa: N802
                parsed = urlparse(self.path)
                if parsed.path != "/v1/correlations":
                    self._json(404, {"error": "not found"})
                    return
                if endpoint.control_status != 200:
                    # The real endpoint answers 404 to any address but the sinkhole's.
                    self._json(endpoint.control_status, {"error": "not found"})
                    return
                wanted = (parse_qs(parsed.query).get("destination_host") or [None])[0]
                with endpoint._lock:
                    endpoint.hint_queries.append(wanted)
                entries = endpoint.pending(wanted)
                self._json(
                    200,
                    {"now": time.time(), "ttl": 120.0, "count": len(entries), "correlations": entries},
                )

            def log_message(self, *args):  # silence
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def register(self, **record) -> dict:
        now = time.time()
        entry = dict(record)
        entry.setdefault("correlation_id", f"c{len(self._hints) + 1:04d}")
        entry.setdefault("ts", now)
        entry["registered_at"] = now
        entry["expires_at"] = now + float(entry.get("ttl") or 120.0)
        with self._lock:
            self._hints.append(entry)
        return entry

    def pending(self, destination_host: str | None = None) -> list[dict]:
        now = time.time()
        with self._lock:
            live = [entry for entry in self._hints if entry["expires_at"] > now]
        if not destination_host:
            return live
        wanted = destination_host.strip().rstrip(".").lower()
        return [entry for entry in live if _host_matches(entry, wanted)]

    def wait_for(self, count: int = 1, timeout: float = 5.0) -> list[dict]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if len(self.events) >= count:
                    return list(self.events)
            time.sleep(0.02)
        with self._lock:
            return list(self.events)

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


def _host_matches(entry: dict, wanted: str) -> bool:
    host = str(entry.get("destination_host") or "").lower()
    return bool(host) and (
        wanted == host or wanted.endswith("." + host) or host.endswith("." + wanted)
    )


@pytest.fixture
def endpoint():
    fake = FakeEndpoint()
    yield fake
    fake.close()


@pytest.fixture
def make_service():
    """Factory for a running service; every port is ephemeral, TLS material is minted."""
    started: list[ResolverService] = []

    def _make(upstream=None, **overrides) -> ResolverService:
        config = Config(
            zone=overrides.pop("zone", ZONE),
            telemetry_url=overrides.pop("telemetry_url", ""),
            listen_host="127.0.0.1",
            dns_udp_port=0,
            dns_tcp_port=0,
            http_port=0,
            https_port=0,
            smtp_port=0,
            ldap_port=0,
            admin_host="127.0.0.1",
            admin_port=0,
            # Pinned so answers are predictable; in production it is derived per client
            # from the route towards it.
            public_ip=overrides.pop("public_ip", "127.0.0.1"),
            hint_poll_interval=overrides.pop("hint_poll_interval", 0.2),
            **overrides,
        )
        service = ResolverService(config, upstream=upstream).start()
        started.append(service)
        return service

    yield _make
    for service in started:
        service.stop()


@pytest.fixture
def service(make_service):
    return make_service()


# -- protocol clients ------------------------------------------------------------


def build_dns_query(name: str, qtype: int = dnswire.TYPE_A, txid: int = 0x4242) -> bytes:
    header = struct.pack("!6H", txid, 0x0100, 1, 0, 0, 0)  # RD set, like a stub resolver
    return header + dnswire.encode_name(name) + struct.pack("!2H", qtype, dnswire.CLASS_IN)


def dns_udp(port: int, name: str, qtype: int = dnswire.TYPE_A, timeout: float = 5.0) -> bytes:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.sendto(build_dns_query(name, qtype), ("127.0.0.1", port))
        return sock.recv(4096)
    finally:
        sock.close()


def dns_tcp(port: int, name: str, qtype: int = dnswire.TYPE_A, timeout: float = 5.0) -> bytes:
    with socket.create_connection(("127.0.0.1", port), timeout=timeout) as sock:
        query = build_dns_query(name, qtype)
        sock.sendall(struct.pack("!H", len(query)) + query)
        length = struct.unpack("!H", _recv_exactly(sock, 2))[0]
        return _recv_exactly(sock, length)


def _recv_exactly(sock: socket.socket, count: int) -> bytes:
    chunks = b""
    while len(chunks) < count:
        chunk = sock.recv(count - len(chunks))
        if not chunk:
            raise AssertionError("connection closed early")
        chunks += chunk
    return chunks


def parse_dns_response(data: bytes) -> dict:
    txid, flags, qdcount, ancount, nscount, _ = struct.unpack("!6H", data[:12])
    pos = 12
    question = None
    if qdcount:
        labels, pos = dnswire.parse_name(data, pos)
        qtype, _qclass = struct.unpack("!2H", data[pos : pos + 4])
        pos += 4
        question = (".".join(label.decode() for label in labels), qtype)
    answers = []
    for _ in range(ancount):
        _labels, pos = dnswire.parse_name(data, pos)
        rrtype, _rrclass, ttl, rdlength = struct.unpack("!HHIH", data[pos : pos + 10])
        pos += 10
        rdata = data[pos : pos + rdlength]
        pos += rdlength
        value = socket.inet_ntoa(rdata) if rrtype == dnswire.TYPE_A else rdata
        answers.append({"type": rrtype, "ttl": ttl, "rdata": value})
    return {
        "txid": txid,
        "flags": flags,
        "rcode": flags & 0x0F,
        "aa": bool(flags & 0x0400),
        "qr": bool(flags & 0x8000),
        "question": question,
        "answers": answers,
        "nscount": nscount,
        "a_records": [a["rdata"] for a in answers if a["type"] == dnswire.TYPE_A],
    }


def http_request(
    port: int,
    target: str = "/",
    *,
    host: str | None = None,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    tls: bool = False,
    server_name: str | None = None,
    timeout: float = 5.0,
) -> bytes:
    """Raw HTTP client: a stdlib one would not let us forge the Host header freely."""
    sock = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    if tls:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        sock = context.wrap_socket(sock, server_hostname=server_name or host or f"edge1.{ZONE}")
    try:
        lines = [f"{method} {target} HTTP/1.1", f"Host: {host or '127.0.0.1'}"]
        lines += [f"{k}: {v}" for k, v in (headers or {}).items()]
        sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
        return sock.recv(65536)
    finally:
        sock.close()


def peer_certificate(port: int, server_name: str, timeout: float = 5.0) -> bytes:
    """Handshake only, returning the DER certificate the listener presented.

    Verification is off, so the parsed form is unavailable; the DER is what the tests
    inspect anyway."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    with socket.create_connection(("127.0.0.1", port), timeout=timeout) as raw:
        with context.wrap_socket(raw, server_hostname=server_name) as tls:
            return tls.getpeercert(binary_form=True) or b""


def admin_get(port: int, path: str, method: str = "GET") -> dict:
    raw = http_request(port, path, method=method).decode("latin-1", "replace")
    _, _, body = raw.partition("\r\n\r\n")
    return json.loads(body)
