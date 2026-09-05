"""The anti-cheat guarantee, asserted as code.

Every score this platform publishes rests on one property: the tool under test can
reach the targets and the canary, and nothing else. If the collector were reachable
from `bench-public`, a scanner could read the answer key (GET /v1/runs/{id}/events
lists exactly which planted sinks fired) or forge trigger events for vulnerabilities
it never found. Both make the numbers fiction.

`internal: true` on the bench-internal network is what enforces it, and a published
port on the collector would route around it. Neither is the kind of thing that should
be protected by a comment in a YAML file, so it is a test.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"
COLLECTOR_NETWORK = "bench-internal"
PUBLIC_NETWORK = "bench-public"


@pytest.fixture(scope="module")
def compose() -> dict:
    assert COMPOSE_FILE.is_file(), f"docker-compose.yml not found at {COMPOSE_FILE}"
    return yaml.safe_load(COMPOSE_FILE.read_text())


def service_networks(service: dict) -> set[str]:
    """Normalise both compose spellings: a list of names or a mapping of names."""
    networks = service.get("networks")
    if networks is None:
        # Compose would attach the service to the implicit default network, which is
        # not internal. For the collector that silently disables the whole guarantee.
        return {"<default>"}
    if isinstance(networks, dict):
        return set(networks)
    return set(networks)


def test_bench_internal_is_declared_internal(compose):
    networks = compose.get("networks", {})
    assert COLLECTOR_NETWORK in networks, "bench-internal network is not declared"
    assert networks[COLLECTOR_NETWORK].get("internal") is True, (
        "bench-internal must be `internal: true`; without it the scanner's network "
        "can route to the collector and read or forge ground truth"
    )


def test_collector_is_attached_only_to_bench_internal(compose):
    collector = compose["services"]["collector"]
    assert service_networks(collector) == {COLLECTOR_NETWORK}


def test_collector_publishes_no_port(compose):
    collector = compose["services"]["collector"]
    assert not collector.get("ports"), (
        "publishing a collector port bypasses the internal network: anything that can "
        "reach the docker host could then read the answer key"
    )
    assert not collector.get("network_mode"), "network_mode overrides the network attachment"


def test_collector_database_is_also_isolated(compose):
    db = compose["services"]["collector-db"]
    assert service_networks(db) == {COLLECTOR_NETWORK}
    assert not db.get("ports")


def test_public_network_is_not_internal(compose):
    """Sanity check on the other half: the tool under test needs its own network,
    and a benchmark where nothing is reachable would pass every isolation test."""
    assert compose["networks"][PUBLIC_NETWORK].get("internal") is not True


def test_no_compose_fragment_redefines_the_collector():
    """Targets are added as fragments under compose/; one of them re-declaring the
    collector service could quietly re-home it onto bench-public."""
    for fragment in sorted((REPO_ROOT / "compose").glob("*.yml")):
        document = yaml.safe_load(fragment.read_text()) or {}
        services = document.get("services") or {}
        assert "collector" not in services, f"{fragment} redefines the collector service"
        assert "collector-db" not in services, f"{fragment} redefines the collector database"
