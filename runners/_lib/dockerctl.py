"""Every docker / docker compose call the harness makes, in one place.

Two reasons this is a class and not a handful of module functions:

* The tests must never start a container. A single seam (``DockerClient._exec``)
  means the whole orchestrator can be driven by a fake, and it is obvious when a new
  code path escapes the seam.
* Reproducibility. Published benchmark numbers are only defensible if the exact
  image digest and argv are recorded, so *constructing* the argv and *recording* it
  happen in the same object rather than in whichever driver felt like it.

Nothing here shells out through a shell: argv lists only. A target URL or a
credential lands in these arguments, and a benchmark harness that can be made to
run arbitrary commands by a crafted app name is not a benchmark harness.
"""

from __future__ import annotations

import json
import os
import logging
import shlex
import subprocess
import time
from collections.abc import Sequence, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("bench.runners.docker")


class DockerError(RuntimeError):
    """A docker invocation failed in a way the harness cannot paper over."""


@dataclass
class ExecResult:
    argv: list[str]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def check(self, what: str) -> ExecResult:
        if not self.ok:
            raise DockerError(
                f"{what} failed (exit {self.returncode}): {shlex.join(self.argv)}\n{self.stderr.strip()}"
            )
        return self


@dataclass
class ContainerHandle:
    """A started container plus the argv that started it, for the run record."""

    container_id: str
    argv: list[str]
    image: str
    image_digest: str | None = None
    log_path: Path | None = None
    _log_proc: Any = field(default=None, repr=False)


class DockerClient:
    """Thin, mockable wrapper over the docker CLI.

    ``docker`` and not the SDK on purpose: the CLI is what a human will type when
    reproducing a published run, so recording the CLI argv means the record is
    directly replayable.
    """

    def __init__(
        self,
        *,
        compose_file: Path | None = None,
        # Matches docker-compose.yml's default. The project name becomes part of
        # every container name and is visible in /etc/hostname inside the targets,
        # so it is unremarkable on purpose and overridable per deployment.
        project: str = "platform-edge",
        docker_bin: str = "docker",
        dry_run: bool = False,
    ):
        self.compose_file = compose_file
        self.project = project
        self.docker_bin = docker_bin
        self.dry_run = dry_run

    # -- seam -------------------------------------------------------------------

    def _exec(
        self,
        argv: Sequence[str],
        *,
        timeout: float | None = 300,
        stdin: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> ExecResult:
        argv = list(argv)
        if self.dry_run:
            log.info("[dry-run] %s", shlex.join(argv))
            return ExecResult(argv, 0, "", "")
        log.debug("exec: %s", shlex.join(argv))
        try:
            proc = subprocess.run(
                argv,
                env=({**os.environ, **env} if env else None),
                input=stdin,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except FileNotFoundError as exc:
            raise DockerError(f"{argv[0]} not found on PATH") from exc
        except subprocess.TimeoutExpired as exc:
            raise DockerError(f"timed out after {timeout}s: {shlex.join(argv)}") from exc
        return ExecResult(argv, proc.returncode, proc.stdout or "", proc.stderr or "")

    def _popen(self, argv: Sequence[str], *, stdout: Any, stderr: Any) -> Any:
        if self.dry_run:
            return None
        return subprocess.Popen(list(argv), stdout=stdout, stderr=stderr, text=True)

    # -- compose ----------------------------------------------------------------

    def _compose_argv(self, *args: str) -> list[str]:
        argv = [self.docker_bin, "compose"]
        if self.compose_file:
            argv += ["-f", str(self.compose_file)]
        if self.project:
            argv += ["-p", self.project]
        return argv + list(args)

    def compose_restart(self, services: Sequence[str], *, timeout: int = 30) -> ExecResult:
        """Restart target services. The reset path; see reset.py for why it is verified."""
        return self._exec(
            self._compose_argv("restart", "-t", str(timeout), *services), timeout=600
        ).check("docker compose restart")

    def compose_ps_id(self, service: str) -> str | None:
        res = self._exec(self._compose_argv("ps", "-q", service))
        if not res.ok:
            return None
        first = res.stdout.strip().splitlines()
        return first[0].strip() if first else None

    def compose_config_json(self) -> str | None:
        """`docker compose config --format json`, or None when it cannot be read.

        Resolved with every profile enabled. Compose omits profiled services entirely
        from an unprofiled `config`, so without this the merged model contains only the
        platform and every question asked of it about a target is answered from a
        document the target does not appear in. That produced a preflight refusal
        stating a target's alias did not exist while the alias resolved and answered
        200 -- a check that is wrong and fatal is worse than the advisory it replaced,
        because it stops every run rather than merely misinforming one.

        Enabling profiles here changes nothing about what runs: this reads the model,
        it does not start anything.
        """
        res = self._exec(
            self._compose_argv("config", "--format", "json"),
            timeout=120,
            env={"COMPOSE_PROFILES": "*"},
        )
        return res.stdout if res.ok else None

    def compose_config(self) -> dict[str, Any] | None:
        """The fully merged compose model, or None when it cannot be read.

        The merged model is the only honest source for "what will actually run":
        it has every fragment's contribution applied, including the ones a target
        added after this harness was written.
        """
        raw = self.compose_config_json()
        if raw is None:
            return None
        try:
            doc = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return doc if isinstance(doc, dict) else None

    def compose_services_by_profile(self) -> dict[str, list[str]] | None:
        """service name -> the compose profiles that gate it.

        Used to refuse a run while a `dev`-profile service is up: those publish a
        target on the host, which is the route the sealed network exists to remove.
        """
        doc = self.compose_config()
        if doc is None:
            return None
        return {
            name: list((spec or {}).get("profiles") or [])
            for name, spec in (doc.get("services") or {}).items()
        }

    def compose_exec(self, service: str, argv: Sequence[str], *, stdin: str | None = None, timeout: float | None = 120) -> ExecResult:
        """Run a command inside a running compose service.

        This is how the harness talks to the collector: the collector sits on the
        `internal: true` bench-internal network with no published port, precisely so
        that the tool under test cannot reach it. Publishing a port for the
        orchestrator's convenience would dissolve the isolation the whole benchmark
        rests on, so the orchestrator borrows the collector's own network namespace
        instead.
        """
        return self._exec(
            self._compose_argv("exec", "-T", service, *argv), stdin=stdin, timeout=timeout
        )

    # -- containers -------------------------------------------------------------

    def inspect(self, ref: str, *, kind: str = "container") -> dict[str, Any] | None:
        res = self._exec([self.docker_bin, "inspect", "--type", kind, ref])
        if not res.ok:
            return None
        try:
            data = json.loads(res.stdout)
        except json.JSONDecodeError:
            return None
        return data[0] if isinstance(data, list) and data else None

    def container_started_at(self, ref: str) -> str | None:
        """``State.StartedAt``. The proof that a restart actually restarted."""
        info = self.inspect(ref)
        if not info:
            return None
        return (info.get("State") or {}).get("StartedAt")

    def image_digest(self, image: str) -> str | None:
        """``RepoDigests[0]`` -- the immutable identity of what was actually run.

        A tag is not a version: `nuclei:latest` in March and in September are two
        different scanners, and a published comparison that only records the tag is
        not reproducible.
        """
        info = self.inspect(image, kind="image")
        if not info:
            return None
        digests = info.get("RepoDigests") or []
        if digests:
            return str(digests[0])
        return str(info.get("Id")) if info.get("Id") else None

    def image_exists(self, image: str) -> bool:
        return self.inspect(image, kind="image") is not None

    def pull(self, image: str) -> ExecResult:
        return self._exec([self.docker_bin, "pull", image], timeout=1800)

    def build(self, image: str, spec: dict[str, Any], *, context_root: Path | None = None) -> ExecResult:
        """Build a local image from a Dockerfile shipped next to its driver.

        Two of the five tools have no usable published image (wapiti publishes none,
        skipfish has none that is current or multi-arch). Building from a pinned
        Dockerfile in this repository is more reproducible than depending on a
        stranger's floating tag, which is the alternative.
        """
        context = Path(spec.get("context", "."))
        if context_root is not None and not context.is_absolute():
            context = context_root / context
        argv = [self.docker_bin, "build", "-t", image]
        dockerfile = spec.get("dockerfile")
        if dockerfile:
            argv += ["-f", str(context / dockerfile)]
        for key, value in (spec.get("args") or {}).items():
            argv += ["--build-arg", f"{key}={value}"]
        argv.append(str(context))
        return self._exec(argv, timeout=3600)

    def ensure_image(
        self,
        image: str,
        *,
        allow_pull: bool = True,
        build_spec: dict[str, Any] | None = None,
        context_root: Path | None = None,
    ) -> str | None:
        """Make the image present and return its digest."""
        if not self.image_exists(image):
            if build_spec:
                self.build(image, build_spec, context_root=context_root).check(f"docker build {image}")
            elif allow_pull:
                self.pull(image).check(f"docker pull {image}")
            else:
                raise DockerError(f"image {image} is absent and pulling is disabled")
        return self.image_digest(image)

    def run_detached(
        self,
        image: str,
        args: Sequence[str],
        *,
        name: str,
        network: str,
        volumes: Sequence[tuple[str, str]] = (),
        env: dict[str, str] | None = None,
        entrypoint: str | None = None,
        user: str | None = None,
        extra_flags: Sequence[str] = (),
        log_path: Path | None = None,
        allow_pull: bool = True,
        build_spec: dict[str, Any] | None = None,
        context_root: Path | None = None,
    ) -> ContainerHandle:
        """Start the tool under test and begin streaming its logs to disk.

        The container is attached to exactly one network (bench-public). Anything
        else and the tool could reach the collector, i.e. the answer key.
        """
        digest = self.ensure_image(
            image, allow_pull=allow_pull, build_spec=build_spec, context_root=context_root
        )
        argv = [self.docker_bin, "run", "-d", "--name", name, "--network", network]
        if entrypoint is not None:
            argv += ["--entrypoint", entrypoint]
        if user is not None:
            argv += ["--user", user]
        for host_path, container_path in volumes:
            argv += ["-v", f"{host_path}:{container_path}"]
        for key, value in (env or {}).items():
            argv += ["-e", f"{key}={value}"]
        argv += list(extra_flags)
        argv += [image, *args]

        res = self._exec(argv, timeout=300).check("docker run")
        container_id = res.stdout.strip().splitlines()[-1].strip() if res.stdout.strip() else name
        handle = ContainerHandle(
            container_id=container_id, argv=argv, image=image, image_digest=digest, log_path=log_path
        )
        if log_path is not None:
            handle._log_proc = self._start_log_stream(container_id, log_path)
        return handle

    def run_capture(
        self,
        image: str,
        args: Sequence[str],
        *,
        entrypoint: str | None = None,
        network: str = "none",
        volumes: Sequence[tuple[str, str]] = (),
        env: dict[str, str] | None = None,
        timeout: float = 180,
        allow_pull: bool = True,
        build_spec: dict[str, Any] | None = None,
        context_root: Path | None = None,
    ) -> ExecResult:
        """Run a short-lived container and capture its output.

        Used for `--version` and for preparation steps. The version string goes into
        the run record next to the image digest, because "nuclei 3.4.10" is what a
        reader recognises while `sha256:...` is what makes the run reproducible.

        Network `none` by default: a version probe has no business talking to the
        targets, and a probe that appeared in the collector's event stream would
        pollute the tool's own crawl coverage. Preparation steps override it with a
        network that has egress -- never the tool's, which is sealed.
        """
        self.ensure_image(
            image, allow_pull=allow_pull, build_spec=build_spec, context_root=context_root
        )
        argv = [self.docker_bin, "run", "--rm", "--network", network]
        if entrypoint is not None:
            argv += ["--entrypoint", entrypoint]
        for host_path, container_path in volumes:
            argv += ["-v", f"{host_path}:{container_path}"]
        for key, value in (env or {}).items():
            argv += ["-e", f"{key}={value}"]
        argv += [image, *args]
        return self._exec(argv, timeout=timeout)

    def _start_log_stream(self, container_id: str, log_path: Path) -> Any:
        """Follow the container's logs into a file for the lifetime of the run.

        Streaming rather than dumping at the end: when a scan is killed by the budget
        (or by the operator), `docker logs` after `docker rm` returns nothing, and the
        log is the only account of what the tool was doing when time ran out.
        """
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handle = log_path.open("a", encoding="utf-8", errors="replace")
        return self._popen(
            [self.docker_bin, "logs", "-f", "--timestamps", container_id],
            stdout=handle,
            stderr=subprocess.STDOUT,
        )

    def container_health(self, ref: str) -> str:
        """``State.Health.Status``, or "none" when the image declares no healthcheck.

        Used instead of an HTTP probe of our own: docker already knows, asking it
        costs the target nothing, and it puts no request from us on the target's
        application port -- which is one less thing that could be mistaken for the
        tool's traffic, and one less thing for a tool to notice.
        """
        state = self.container_state(ref)
        if not state:
            return "unknown"
        health = state.get("Health") or {}
        status = health.get("Status")
        if status:
            return str(status)
        return "none" if state.get("Running") else "stopped"

    def container_state(self, container_id: str) -> dict[str, Any]:
        info = self.inspect(container_id)
        return (info or {}).get("State") or {}

    def is_running(self, container_id: str) -> bool:
        return bool(self.container_state(container_id).get("Running"))

    def exit_code(self, container_id: str) -> int | None:
        state = self.container_state(container_id)
        if state.get("Running"):
            return None
        code = state.get("ExitCode")
        return int(code) if code is not None else None

    def stop(self, container_id: str, *, grace: int = 20) -> ExecResult:
        """Stop with a grace period so the tool can flush its report.

        ZAP and Arachni both write their report at shutdown; SIGKILLing them at the
        budget deadline would throw away the findings we spent the budget producing.
        """
        return self._exec([self.docker_bin, "stop", "-t", str(grace), container_id], timeout=grace + 60)

    def rm(self, container_id: str) -> ExecResult:
        return self._exec([self.docker_bin, "rm", "-f", container_id])

    def close_logs(self, handle: ContainerHandle, *, timeout: float = 15) -> None:
        proc = handle._log_proc
        if proc is None:
            return
        deadline = time.monotonic() + timeout
        while proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.2)
        if proc.poll() is None:
            proc.terminate()
