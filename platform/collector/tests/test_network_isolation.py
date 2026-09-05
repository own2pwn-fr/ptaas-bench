"""The anti-cheat guarantee, asserted as code.

Every score this platform publishes rests on one property: the tool under test can
reach the targets and the sinkhole, and nothing else. If this service were reachable
from `bench-public`, a scanner could read the answer key (GET /v1/runs/{id}/events
lists exactly which planted sinks fired) or forge trigger events for vulnerabilities
it never found. Both make the numbers fiction.

`internal: true` on the bench-internal network is what enforces it, and a published
port would route around it. Neither is the kind of thing that should be protected by
a comment in a YAML file, so it is a test.

The service is identified by its build context rather than by its name: it is
deliberately named after ordinary telemetry infrastructure (`otel-collector`) and may
be renamed again for the same reason, but a rename must never silently take the
network configuration with it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"
COLLECTOR_BUILD = "./platform/collector"
INTERNAL_NETWORK = "bench-internal"
PUBLIC_NETWORK = "bench-public"

# From targets/target-contract.yaml, section `deception`. Anything a tool can observe
# -- and a container name on its own network is observable -- must not announce that
# this is an evaluation.
FORBIDDEN_MARKERS = (
    "bench", "vuln", "insecure", "ctf", "flag", "challenge", "lab", "testbed",
    "sandbox", "honeypot", "canary", "oracle", "trigger", "dvwa", "juice-shop",
    "scanner", "evaluation", "scoring", "grader",
)


@pytest.fixture(scope="module")
def compose() -> dict:
    assert COMPOSE_FILE.is_file(), f"docker-compose.yml not found at {COMPOSE_FILE}"
    return yaml.safe_load(COMPOSE_FILE.read_text())


def service_networks(service: dict) -> set[str]:
    """Normalise both compose spellings: a list of names or a mapping of names."""
    networks = service.get("networks")
    if networks is None:
        # Compose would attach the service to the implicit default network, which is
        # not internal. For this service that silently disables the whole guarantee.
        return {"<default>"}
    if isinstance(networks, dict):
        return set(networks)
    return set(networks)


def build_context(service: dict) -> str | None:
    build = service.get("build")
    if isinstance(build, dict):
        return build.get("context")
    return build


def collector_service(compose: dict) -> tuple[str, dict]:
    matches = [
        (name, service)
        for name, service in compose["services"].items()
        if build_context(service) == COLLECTOR_BUILD
    ]
    assert len(matches) == 1, f"expected exactly one {COLLECTOR_BUILD} service, found {matches}"
    return matches[0]


def test_bench_internal_is_declared_internal(compose):
    networks = compose.get("networks", {})
    assert INTERNAL_NETWORK in networks, "the internal network is not declared"
    assert networks[INTERNAL_NETWORK].get("internal") is True, (
        "bench-internal must be `internal: true`; without it the scanner's network "
        "can route to the collector and read or forge ground truth"
    )


def test_collector_is_attached_only_to_bench_internal(compose):
    _, collector = collector_service(compose)
    assert service_networks(collector) == {INTERNAL_NETWORK}


def test_collector_is_not_named_after_the_benchmark(compose):
    """Deception: targets are dual-homed, so a tool with RCE on one resolves and reads
    the endpoint in its environment. `otel-collector` is unremarkable; a name that
    says "bench" is a confession."""
    name, _ = collector_service(compose)
    assert not any(marker in name.lower() for marker in FORBIDDEN_MARKERS), name


def test_collector_publishes_no_port(compose):
    _, collector = collector_service(compose)
    assert not collector.get("ports"), (
        "publishing a collector port bypasses the internal network: anything that can "
        "reach the docker host could then read the answer key"
    )
    assert not collector.get("network_mode"), "network_mode overrides the network attachment"


def test_collector_database_is_also_isolated(compose):
    databases = [
        service
        for service in compose["services"].values()
        if str(service.get("image", "")).startswith("postgres")
    ]
    assert databases, "no database service found"
    for database in databases:
        assert service_networks(database) == {INTERNAL_NETWORK}
        assert not database.get("ports")


def test_public_network_is_not_internal(compose):
    """Sanity check on the other half: the tool under test needs its own network,
    and a benchmark where nothing is reachable would pass every isolation test."""
    assert compose["networks"][PUBLIC_NETWORK].get("internal") is not True


def test_services_on_the_public_network_are_not_named_after_the_benchmark(compose):
    """Whatever sits on bench-public is resolvable by the tool under test, so its
    service name, container name and hostname are part of the deception surface."""
    for name, service in compose["services"].items():
        if PUBLIC_NETWORK not in service_networks(service):
            continue
        observable = [name, service.get("container_name") or "", service.get("hostname") or ""]
        for value in observable:
            assert not any(marker in value.lower() for marker in FORBIDDEN_MARKERS), (
                f"service {name!r} exposes {value!r} on the tool's network"
            )


def test_no_compose_fragment_redefines_the_collector():
    """Targets are added as fragments under compose/; one of them re-declaring the
    collector service could quietly re-home it onto bench-public."""
    for fragment in sorted((REPO_ROOT / "compose").glob("*.yml")):
        document = yaml.safe_load(fragment.read_text()) or {}
        services = document.get("services") or {}
        for name, service in services.items():
            assert build_context(service) != COLLECTOR_BUILD, f"{fragment} redefines {name}"
