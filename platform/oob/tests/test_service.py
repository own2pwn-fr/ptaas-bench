"""Service wiring, store bounds, the fail-closed admin binding, and the probe."""

from __future__ import annotations

from edge_resolver.config import Config
from edge_resolver.correlation import Attribution
from edge_resolver.recorder import RAW_MAX, Recorder
from edge_resolver.service import ResolverService
from edge_resolver.store import ObservationStore
from edge_resolver.tokens import Candidate

from conftest import ZONE, dns_udp


def test_every_listener_is_bound(service):
    assert set(service.ports) == {"dns_udp", "dns_tcp", "http", "https", "smtp", "ldap", "admin"}
    assert all(port > 0 for port in service.ports.values())


def test_admin_binding_fails_closed_without_an_endpoint():
    """If we cannot prove which interface is the internal one, the safe failure is
    unreachable, not readable by everything on the application network."""
    assert ResolverService(Config(telemetry_url="", admin_host="auto"))._admin_host() == "127.0.0.1"


def test_admin_binding_follows_the_endpoint_route():
    service = ResolverService(Config(telemetry_url="http://127.0.0.1:8900", admin_host="auto"))
    assert service._admin_host() == "127.0.0.1"


def test_store_is_bounded(make_service):
    service = make_service(store_size=3)
    for index in range(5):
        dns_udp(service.ports["dns_udp"], f"aaaa000{index}.{ZONE}")
    service.store.wait_for(3, timeout=5)
    assert len(service.store.all()) <= 3
    assert service.store.last_seq() == 5  # the sequence keeps counting past evictions


def test_raw_is_truncated_to_the_protocol_limit():
    class NullTelemetry:
        def submit(self, event):
            self.event = event

    class NullIndex:
        def match(self, host, source_ip, now=None):
            return Attribution()

    telemetry = NullTelemetry()
    store = ObservationStore()
    recorder = Recorder(Config(zone=ZONE), store, telemetry, NullIndex())
    record = recorder.record(
        channel="http",
        source_ip="127.0.0.1",
        candidates=[Candidate("path_segment", "shop0031")],
        raw="A" * 5000,
    )
    assert len(record.raw) <= RAW_MAX
    assert len(telemetry.event["raw"]) <= RAW_MAX
    assert record.raw.endswith("src=path_segment origin=n/a conf=none")


def test_probe_asks_the_admin_api_and_writes_nothing(service, monkeypatch):
    """A health tick every ten seconds must not write a phantom record into every run."""
    from edge_resolver import healthcheck

    monkeypatch.setenv("RESOLVER_ADMIN_HOST", "127.0.0.1")
    monkeypatch.setenv("RESOLVER_ADMIN_PORT", str(service.ports["admin"]))
    assert healthcheck.main() == 0
    assert len(service.store) == 0


def test_probe_fails_when_nothing_listens(monkeypatch):
    from edge_resolver import healthcheck

    monkeypatch.setenv("RESOLVER_ADMIN_HOST", "127.0.0.1")
    monkeypatch.setenv("RESOLVER_ADMIN_PORT", "1")
    assert healthcheck.main() == 1
