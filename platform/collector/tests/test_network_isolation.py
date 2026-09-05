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

import ipaddress
import re
from pathlib import Path

import pytest
import yaml

from bench_collector.config import load_settings, parse_cidrs

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"
COLLECTOR_BUILD = "./platform/collector"
SINKHOLE_BUILD = "./platform/oob"
INTERNAL_NETWORK = "bench-internal"
PUBLIC_NETWORK = "bench-public"
COLLECTOR_PORT = 8900

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


def service_by_build(compose: dict, context: str) -> tuple[str, dict]:
    matches = [
        (name, service)
        for name, service in compose["services"].items()
        if build_context(service) == context
    ]
    assert len(matches) == 1, f"expected exactly one {context} service, found {matches}"
    return matches[0]


def collector_service(compose: dict) -> tuple[str, dict]:
    return service_by_build(compose, COLLECTOR_BUILD)


def pinned_address(service: dict, network: str) -> str | None:
    networks = service.get("networks")
    if isinstance(networks, dict) and isinstance(networks.get(network), dict):
        return networks[network].get("ipv4_address")
    return None


def environment(service: dict) -> dict[str, str]:
    """Normalise both compose spellings of `environment`."""
    env = service.get("environment") or {}
    if isinstance(env, list):
        pairs = [item.split("=", 1) for item in env]
        return {key: value for key, *rest in pairs for value in (rest or [""])}
    return {key: "" if value is None else str(value) for key, value in env.items()}


def test_bench_internal_is_declared_internal(compose):
    networks = compose.get("networks", {})
    assert INTERNAL_NETWORK in networks, "the internal network is not declared"
    assert networks[INTERNAL_NETWORK].get("internal") is True, (
        "bench-internal must be `internal: true`; without it the scanner's network "
        "can route to the collector and read or forge ground truth"
    )


def test_bench_public_is_internal_too(compose):
    """No route out is what makes blind vulnerabilities measurable rather than merely
    unreachable: a callback to the tool's own collaborator domain cannot leave, so the
    sinkhole captures it. It also takes the host gateway away from a compromised
    target."""
    assert compose["networks"][PUBLIC_NETWORK].get("internal") is True


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


def test_networks_are_separate_and_fixed(compose):
    """Two distinct networks with pinned subnets. Fixed addressing is load-bearing:
    the collector's control allowlist and the targets' `dns:` pin are written against
    these addresses, and a benchmark where the two networks collapsed into one would
    pass every other assertion here."""
    subnets = {
        name: compose["networks"][name]["ipam"]["config"][0]["subnet"]
        for name in (INTERNAL_NETWORK, PUBLIC_NETWORK)
    }
    assert subnets[INTERNAL_NETWORK] != subnets[PUBLIC_NETWORK]
    internal = ipaddress.ip_network(subnets[INTERNAL_NETWORK])
    public = ipaddress.ip_network(subnets[PUBLIC_NETWORK])
    assert not internal.overlaps(public), subnets


def test_control_allowlist_matches_the_sinkholes_pinned_address(compose):
    """The control surface -- run management and the event export that lists exactly
    which planted sinks fired -- is allowlisted by address. If the sinkhole is
    renumbered and this is not, the platform either locks out its own sinkhole or,
    far worse, hands the allowlisted address to whichever container claims it next.
    """
    _, collector = collector_service(compose)
    _, sinkhole = service_by_build(compose, SINKHOLE_BUILD)
    sinkhole_address = pinned_address(sinkhole, INTERNAL_NETWORK)
    assert sinkhole_address, "the sinkhole must have a pinned address to be allowlisted"

    internal = ipaddress.ip_network(compose["networks"][INTERNAL_NETWORK]["ipam"]["config"][0]["subnet"])
    assert ipaddress.ip_address(sinkhole_address) in internal

    env = environment(collector)
    control = parse_cidrs(env["TELEMETRY_CONTROL_CIDRS"])
    assert control, "the control surface must be restricted by source address"
    routable = [network for network in control if not network.network_address.is_loopback]
    assert [str(network) for network in routable] == [f"{sinkhole_address}/32"], (
        "only the sinkhole (and the container's own loopback, which is how the "
        "orchestrator reaches it through `docker compose exec`) may reach the answer key"
    )

    # Platform traffic is recognised by source address now that the selftest header is
    # gone; the same pin has to identify it.
    synthetic = parse_cidrs(env["TELEMETRY_SYNTHETIC_CIDRS"])
    assert [str(network) for network in synthetic] == [f"{sinkhole_address}/32"]


def test_collector_reads_the_environment_the_stack_gives_it(compose):
    """Cross-check the deployed configuration against the program that consumes it: a
    renamed variable that nothing reads fails open and silently."""
    _, collector = collector_service(compose)
    env = environment(collector)
    assert not any(key.startswith("BENCH_") for key in env), sorted(env)

    settings = load_settings(env)
    assert settings.database_url.startswith("postgresql+asyncpg://")
    assert settings.control_networks and settings.synthetic_networks
    assert settings.expose_schema is False, "the API description must not be published"


def test_orchestrator_reaches_the_control_plane_without_a_published_port(compose):
    """`docker compose exec` rather than a published port, so the invariant that the
    collector publishes nothing stays absolute."""
    _, collector = collector_service(compose)
    control = parse_cidrs(environment(collector)["TELEMETRY_CONTROL_CIDRS"])
    assert any(network.network_address.is_loopback for network in control)
    for service in compose["services"].values():
        for mapping in service.get("ports") or []:
            assert str(COLLECTOR_PORT) not in str(mapping), (
                f"port {COLLECTOR_PORT} must never be published: {mapping}"
            )


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


def test_default_project_name_is_unremarkable(compose):
    """Container names derive from it, they appear in /etc/hostname inside every
    target, and Docker's embedded DNS resolves them on the tool's own network."""
    raw = str(compose["name"])
    match = re.fullmatch(r"\$\{[A-Za-z_][A-Za-z0-9_]*:-([^}]+)\}", raw)
    default = match.group(1) if match else raw
    assert default, raw
    assert not any(marker in default.lower() for marker in FORBIDDEN_MARKERS), default


def test_no_compose_fragment_reopens_an_internal_network():
    """Fragments re-declare the shared networks so that `include` merges instead of
    conflicting. Omitting a key is harmless -- the merge keeps the base value -- but
    setting `internal: false` would quietly reconnect the tool's network to the world
    and make every blind-vulnerability score meaningless."""
    for fragment in sorted((REPO_ROOT / "compose").glob("*.yml")):
        document = yaml.safe_load(fragment.read_text()) or {}
        for name, network in (document.get("networks") or {}).items():
            if name in (INTERNAL_NETWORK, PUBLIC_NETWORK) and isinstance(network, dict):
                assert network.get("internal") is not False, f"{fragment} reopens {name}"


def test_no_compose_fragment_redefines_the_collector():
    """Targets are added as fragments under compose/; one of them re-declaring the
    collector service could quietly re-home it onto bench-public."""
    for fragment in sorted((REPO_ROOT / "compose").glob("*.yml")):
        document = yaml.safe_load(fragment.read_text()) or {}
        services = document.get("services") or {}
        for name, service in services.items():
            assert build_context(service) != COLLECTOR_BUILD, f"{fragment} redefines {name}"
