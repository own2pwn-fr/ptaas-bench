"""Doubles for docker and HTTP, so the run lifecycle can be tested without either.

The point of the seam is not testing convenience. It is that a benchmark harness has
to be verifiable on a laptop with no images pulled, in CI, and next to another
agent's scan -- and that the interesting failure modes (a restart that did not
restart, a budget that did not fire) are precisely the ones that are impossible to
provoke reliably against real containers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from runners._lib.dockerctl import ContainerHandle, ExecResult
from runners._lib.internal_http import Response


class FakeClock:
    """A clock that only moves when the code under test sleeps."""

    def __init__(self, start: float = 1000.0):
        self.now = start
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds

    def advance(self, seconds: float) -> None:
        self.now += seconds


@dataclass
class FakeDocker:
    """Implements exactly the surface of DockerClient that the harness uses."""

    started_at: dict[str, str] = field(default_factory=dict)
    # service -> ExecResult (or a queue of them) for `docker compose exec`
    exec_results: dict[str, Any] = field(default_factory=dict)
    # container ref -> State.Health.Status
    health: dict[str, str] = field(default_factory=dict)
    # container ref -> the `docker inspect` payload the topology capture reads
    containers: dict[str, Any] = field(default_factory=dict)
    # container id -> how many polls it stays "running" before exiting
    runs_for_polls: dict[str, int] = field(default_factory=dict)
    default_runs_for_polls: int = 10_000  # effectively "never exits on its own"
    exit_codes: dict[str, int] = field(default_factory=dict)
    restart_advances_clock: bool = True
    calls: list[tuple[str, Any]] = field(default_factory=list)
    stopped: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    started: list[dict[str, Any]] = field(default_factory=list)
    _poll_counts: dict[str, int] = field(default_factory=dict)
    _restarts: int = 0

    # -- compose ---------------------------------------------------------------

    def compose_restart(self, services, timeout: int = 30):
        self.calls.append(("compose_restart", list(services)))
        self._restarts += 1
        if self.restart_advances_clock:
            for service in services:
                cid = self.compose_ps_id(service)
                self.started_at[cid] = f"2026-09-05T18:0{self._restarts}:00.123456789Z"
        return ExecResult(["docker", "compose", "restart"], 0, "", "")

    def compose_ps_id(self, service: str) -> str:
        return f"cid-{service}"

    def compose_exec(self, service: str, argv, *, stdin=None, timeout=None):
        self.calls.append(("compose_exec", (service, list(argv))))
        # Scripted per service, so a test can make the reset command fail, print a
        # different digest each time (a non-deterministic reset), or print nothing.
        scripted = self.exec_results.get(service)
        if scripted is None:
            return ExecResult(["docker", "compose", "exec"], 0, "{}", "")
        if isinstance(scripted, list):
            return scripted.pop(0) if len(scripted) > 1 else scripted[0]
        return scripted

    def container_started_at(self, ref: str) -> str | None:
        return self.started_at.get(ref)

    def container_health(self, ref: str) -> str:
        return self.health.get(ref, "healthy")

    def inspect(self, ref: str, *, kind: str = "container"):
        if kind == "image":
            return {"RepoDigests": [f"{ref}@sha256:fake"], "Id": f"sha256:id-{ref}"}
        return self.containers.get(ref)

    # -- containers ------------------------------------------------------------

    def run_detached(self, image, args, *, name, network, volumes=(), env=None,
                     entrypoint=None, user=None, extra_flags=(), log_path=None,
                     allow_pull=True, build_spec=None, context_root=None):
        argv = ["docker", "run", "-d", "--name", name, "--network", network, image, *args]
        self.started.append(
            {
                "image": image,
                "args": list(args),
                "name": name,
                "network": network,
                "volumes": list(volumes),
                "env": dict(env or {}),
                "entrypoint": entrypoint,
                "log_path": log_path,
            }
        )
        self.calls.append(("run_detached", name))
        return ContainerHandle(
            container_id=name, argv=argv, image=image, image_digest=f"{image}@sha256:fake"
        )

    def is_running(self, container_id: str) -> bool:
        count = self._poll_counts.get(container_id, 0)
        self._poll_counts[container_id] = count + 1
        limit = self.runs_for_polls.get(container_id, self.default_runs_for_polls)
        return count < limit

    def exit_code(self, container_id: str) -> int | None:
        return self.exit_codes.get(container_id, 0)

    def stop(self, container_id: str, *, grace: int = 20):
        self.stopped.append(container_id)
        return ExecResult(["docker", "stop"], 0, "", "")

    def rm(self, container_id: str):
        self.removed.append(container_id)
        return ExecResult(["docker", "rm"], 0, "", "")

    def close_logs(self, handle, *, timeout: float = 15) -> None:
        self.calls.append(("close_logs", handle.container_id))

    def run_capture(self, image, args, *, entrypoint=None, network="none", volumes=(),
                    env=None, timeout=180, allow_pull=True, build_spec=None,
                    context_root=None):
        self.calls.append(("run_capture", (image, list(args), network)))
        return ExecResult(["docker", "run", "--rm"], 0, "FakeTool 1.2.3\n", "")

    def image_digest(self, image: str) -> str:
        # Shaped like a real RepoDigests[0], because the run record's whole claim is
        # that the digest it prints could be fed back to `docker run`.
        return f"{image}@sha256:fake"

    def ensure_image(self, image: str, **kwargs) -> str:
        return self.image_digest(image)


class FakeHttp:
    """Routes URL -> Response (or a callable returning one, for stateful endpoints)."""

    def __init__(self, routes: dict[str, Any] | None = None, default: Response | None = None):
        self.routes = routes or {}
        self.default = default or Response(404, "no route in the fake")
        self.requests: list[tuple[str, str]] = []
        self.connected_to: list[str | None] = []

    def request(self, method: str, url: str, *, json_body=None, data=None, headers=None,
                timeout: float = 30, connect_to: str | None = None) -> Response:
        # connect_to is recorded rather than applied: the routing decision is what
        # matters to the caller, and a test asserts it was made.
        self.requests.append((method.upper(), url))
        self.connected_to.append(connect_to)
        handler = self.routes.get(url, self.default)
        if isinstance(handler, list):
            # A queue: successive calls to the same URL return successive answers,
            # which is how "unhealthy, unhealthy, then healthy" is expressed.
            return handler.pop(0) if len(handler) > 1 else handler[0]
        if callable(handler):
            return handler(method, url, json_body, headers)
        return handler


def json_response(payload: dict[str, Any], status: int = 200) -> Response:
    import json

    return Response(status, json.dumps(payload))


def reset_ok(digest: str = "sha256:seeded-1") -> ExecResult:
    """What a conforming /usr/local/bin/state-reset prints."""
    return ExecResult(["docker", "compose", "exec"], 0, f"{digest}\n", "")


def container_inspect(
    name: str,
    *,
    image: str = "nginx:1.21.6-alpine",
    addresses: dict[str, str] | None = None,
    started_at: str = "2026-09-05T18:01:00.000000000Z",
) -> dict[str, Any]:
    """A `docker inspect` payload with the fields the topology capture reads."""
    return {
        "Name": f"/{name}",
        "Image": f"sha256:id-{image}",
        "Config": {"Image": image},
        "State": {"Running": True, "StartedAt": started_at, "Health": {"Status": "healthy"}},
        "NetworkSettings": {
            "Networks": {
                network: {"IPAddress": ip}
                for network, ip in (addresses or {"bench-public": "10.88.0.3"}).items()
            }
        },
    }
