"""Shared fixtures: a live service on ephemeral ports, plus dumb protocol clients.

The tests drive the real listeners over real sockets rather than calling handlers
directly, because most of what can break in this component is wire format, not logic.
Privileged ports (53/80/443/25/389) only exist inside the container; here every
listener binds port 0 and the assigned port is read back from ``service.ports``.
"""

from __future__ import annotations

import json
import socket
import ssl
import struct
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from bench_oob import dnswire
from bench_oob.config import Config
from bench_oob.service import OobService

ZONE = "oob.bench.local"


@pytest.fixture
def make_service():
    """Factory for a running service; every port is ephemeral, TLS cert is generated."""
    started: list[OobService] = []

    def _make(**overrides) -> OobService:
        config = Config(
            domain=overrides.pop("domain", ZONE),
            collector_url=overrides.pop("collector_url", ""),
            listen_host="127.0.0.1",
            dns_udp_port=0,
            dns_tcp_port=0,
            http_port=0,
            https_port=0,
            smtp_port=0,
            ldap_port=0,
            control_host="127.0.0.1",
            control_port=0,
            # Pinned so A answers are predictable; in production this is derived per
            # client from the route towards it.
            public_ip=overrides.pop("public_ip", "127.0.0.1"),
            **overrides,
        )
        service = OobService(config).start()
        started.append(service)
        return service

    yield _make
    for service in started:
        service.stop()


@pytest.fixture
def service(make_service):
    return make_service()


class FakeCollector:
    """Minimal stand-in for POST /v1/events that remembers what it was sent."""

    def __init__(self, status: int = 202) -> None:
        self.events: list[dict] = []
        self.requests = 0
        self._lock = threading.Lock()
        collector = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(length) or b"{}")
                with collector._lock:
                    collector.requests += 1
                    collector.events.extend(payload.get("events", []))
                self.send_response(status)
                self.end_headers()

            def log_message(self, *args):  # silence
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def wait_for(self, count: int = 1, timeout: float = 5.0) -> list[dict]:
        import time

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


@pytest.fixture
def fake_collector():
    collector = FakeCollector()
    yield collector
    collector.close()


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
    """Decode header counts, the question and every answer record we care about."""
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
    }


def http_request(
    port: int,
    target: str = "/",
    *,
    host: str | None = None,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    tls: bool = False,
    timeout: float = 5.0,
) -> bytes:
    """Raw HTTP client: a stdlib one would not let us forge the Host header freely."""
    sock = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    if tls:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        sock = context.wrap_socket(sock, server_hostname=host or "oob.bench.local")
    try:
        lines = [f"{method} {target} HTTP/1.1", f"Host: {host or '127.0.0.1'}"]
        lines += [f"{k}: {v}" for k, v in (headers or {}).items()]
        sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
        return sock.recv(65536)
    finally:
        sock.close()


def control_get(port: int, path: str, method: str = "GET") -> dict:
    raw = http_request(port, path, method=method).decode("latin-1", "replace")
    _, _, body = raw.partition("\r\n\r\n")
    return json.loads(body)
