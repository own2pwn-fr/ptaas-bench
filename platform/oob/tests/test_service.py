"""Service wiring, store bounds and the fail-closed control binding."""

from __future__ import annotations

from bench_oob.config import Config
from bench_oob.recorder import RAW_MAX, Recorder
from bench_oob.service import OobService
from bench_oob.store import CallbackStore
from bench_oob.tokens import Candidate

from conftest import ZONE, dns_udp


def test_every_listener_is_bound(service):
    assert set(service.ports) == {"dns_udp", "dns_tcp", "http", "https", "smtp", "ldap", "control"}
    assert all(port > 0 for port in service.ports.values())


def test_control_binding_fails_closed_without_a_collector():
    """If we cannot prove which interface faces bench-internal, the control API must be
    unreachable rather than exposed to the tool under test on bench-public."""
    service = OobService(Config(collector_url="", control_host="auto"))
    assert service._control_host() == "127.0.0.1"


def test_control_binding_follows_the_collector_route():
    service = OobService(
        Config(collector_url="http://127.0.0.1:8900", control_host="auto")
    )
    assert service._control_host() == "127.0.0.1"


def test_store_is_bounded(make_service):
    service = make_service(store_size=3)
    for index in range(5):
        dns_udp(service.ports["dns_udp"], f"aaaa000{index}.{ZONE}")
    service.store.wait_for(3, timeout=5)
    stored = service.store.all()
    assert len(stored) <= 3
    assert service.store.last_seq() == 5  # the sequence keeps counting past evictions


def test_raw_is_truncated_to_the_schema_limit():
    class NullCollector:
        def submit(self, event):
            self.event = event

    collector = NullCollector()
    store = CallbackStore()
    recorder = Recorder(Config(domain=ZONE), store, collector)
    callback = recorder.record(
        channel="http",
        source_ip="127.0.0.1",
        candidates=[Candidate("path_segment", "shop0031")],
        raw="A" * 5000,
    )
    assert len(callback.raw) <= RAW_MAX
    assert len(collector.event["raw"]) <= RAW_MAX
    assert callback.raw.endswith("src=path_segment zone=n/a")


def test_healthcheck_probes_the_control_api(service, monkeypatch):
    """The probe must not touch a callback listener: a health tick every 10s would
    otherwise write a phantom callback into every run."""
    from bench_oob import healthcheck

    monkeypatch.setenv("BENCH_OOB_CONTROL_HOST", "127.0.0.1")
    monkeypatch.setenv("BENCH_OOB_CONTROL_PORT", str(service.ports["control"]))
    assert healthcheck.main() == 0
    assert len(service.store) == 0


def test_healthcheck_fails_when_nothing_listens(monkeypatch):
    from bench_oob import healthcheck

    monkeypatch.setenv("BENCH_OOB_CONTROL_HOST", "127.0.0.1")
    monkeypatch.setenv("BENCH_OOB_CONTROL_PORT", "1")
    assert healthcheck.main() == 1
