"""The address map of the targets, captured at run open.

WHY THIS EXISTS
Attributing an out-of-band callback means answering "which application made this
connection?", and the answer is an address. Two components see an address for the
same container and they are different addresses: a correlation hint is registered
over bench-internal, so the collector stamps 10.77.0.x, while the callback itself
leaves over bench-public, so the sinkhole observes 10.88.0.x. Same container, two
networks, and no arithmetic relationship between the two octets. Anything that infers
one from the other is wrong for every dual-homed target, which is all of them.

The orchestrator is the only component that can see the truth, because it is the only
one talking to the docker daemon. So it inspects every target container at run open,
after the stack is up and the reset is verified, and hands the collector a map of
which addresses belong to which application.

WHY AT RUN OPEN, EVERY TIME
Container addresses are reassigned when containers restart, and the reset path
restarts containers on purpose. A map captured at run open is the only version that
is true for that run; a cached one eventually attributes one tool's callback to
another tool's target, and it does so silently. That is the worst failure mode
available here: blind vulnerabilities are precisely what this platform exists to
measure, and a misattributed callback is not an error, it is a plausible wrong
number.

WHAT ELSE GOES IN
The image actually running for each target, by digest and by content id rather than
by tag. "The target was at nginx:1.21.6-alpine" is not a re-runnable statement: tags
move, and for this corpus the exact parser behaviour of each hop *is* the
vulnerability. And the state digest read before and after the run, which is what
proves the target was in its seeded state while this tool was measured.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .config import AppSpec

log = logging.getLogger("bench.runners.topology")


@dataclass
class ServiceTopology:
    """One container of one target application, as docker sees it right now."""

    service: str
    container_id: str | None = None
    container_name: str | None = None
    # The image reference the compose file asked for -- a tag, usually.
    image: str | None = None
    # The content id of what is actually running. Always available, including for
    # images built locally from this repository, which have no registry digest.
    image_id: str | None = None
    # RepoDigests[0]: the immutable registry identity. None for a locally built image.
    image_digest: str | None = None
    # network name -> address on that network.
    addresses: dict[str, str] = field(default_factory=dict)
    started_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "service": self.service,
            "container_id": self.container_id,
            "container_name": self.container_name,
            "image": self.image,
            "image_id": self.image_id,
            "image_digest": self.image_digest,
            "addresses": [
                {"network": network, "ip": ip} for network, ip in sorted(self.addresses.items())
            ],
            "started_at": self.started_at,
        }


@dataclass
class AppTopology:
    app: str
    services: list[ServiceTopology] = field(default_factory=list)
    # State digest from the reset command, before and after the run. Equal values are
    # the evidence that this tool was measured against the seeded state and left it
    # that way; unequal values mean the next run's reset check will fail, loudly.
    state_digest_before: str | None = None
    state_digest_after: str | None = None

    @property
    def addresses(self) -> list[str]:
        """Every address this application answers on, across every network.

        The flat list is what an address-keyed lookup actually needs; the structured
        per-service view above is for the human reading the record afterwards.
        """
        seen: list[str] = []
        for service in self.services:
            for ip in service.addresses.values():
                if ip and ip not in seen:
                    seen.append(ip)
        return seen

    def to_dict(self) -> dict[str, Any]:
        return {
            "app": self.app,
            "addresses": self.addresses,
            "services": [s.to_dict() for s in self.services],
            "state_digest_before": self.state_digest_before,
            "state_digest_after": self.state_digest_after,
        }


def inspect_app(docker: Any, app: AppSpec) -> AppTopology:
    topology = AppTopology(app=app.key)
    for service in app.services:
        entry = ServiceTopology(service=service)
        container_id = docker.compose_ps_id(service)
        if not container_id:
            # Not running. Recorded rather than skipped: a target that is down during
            # a run explains an empty result far better than a missing line does.
            log.warning("%s: service %s has no running container", app.key, service)
            topology.services.append(entry)
            continue
        entry.container_id = container_id
        info = docker.inspect(container_id) or {}
        entry.container_name = (info.get("Name") or "").lstrip("/") or None
        entry.image_id = info.get("Image")
        entry.image = ((info.get("Config") or {}).get("Image")) or None
        entry.started_at = (info.get("State") or {}).get("StartedAt")
        networks = ((info.get("NetworkSettings") or {}).get("Networks")) or {}
        for network, spec in networks.items():
            address = (spec or {}).get("IPAddress")
            if address:
                entry.addresses[network] = address
        if entry.image:
            entry.image_digest = docker.image_digest(entry.image)
        topology.services.append(entry)

    if not topology.addresses:
        log.warning(
            "%s: no container addresses found. Out-of-band callbacks from this target "
            "cannot be attributed to it, so blind findings will be lost rather than "
            "wrong -- check that the stack is up before opening the run.",
            app.key,
        )
    return topology


def inspect_apps(docker: Any, apps: list[AppSpec]) -> dict[str, AppTopology]:
    return {app.key: inspect_app(docker, app) for app in apps}


def address_payload(topologies: dict[str, AppTopology]) -> dict[str, Any]:
    """The map as sent to the collector with ``POST /v1/runs``.

    Deliberately flat and boring: one entry per application, every address it holds,
    plus the per-service detail. The consumer needs address -> app; everything else
    is there so a human can check the mapping rather than trust it.
    """
    return {app: topology.to_dict() for app, topology in topologies.items()}


def duplicate_addresses(topologies: dict[str, AppTopology]) -> dict[str, list[str]]:
    """Addresses claimed by more than one application.

    Should be empty. If it is not, attribution by address is ambiguous for those
    addresses and any blind finding involving them is unsafe to publish, so the
    orchestrator says so out loud rather than letting the collector pick a winner.
    """
    owners: dict[str, list[str]] = {}
    for app, topology in topologies.items():
        for address in topology.addresses:
            owners.setdefault(address, []).append(app)
    return {address: apps for address, apps in owners.items() if len(apps) > 1}
