"""The driver interface every tool implements, and the container run loop.

A driver answers two questions and nothing else:

* ``plan(ctx)``      -- how do I invoke this tool against these targets, with these
  credentials, inside this budget? It returns ``Invocation`` objects (argv, mounts,
  env) and may write config files (a ZAP plan, a wapiti session dir) into the run
  directory, where they are kept as part of the evidence.
* ``normalise(...)`` -- how do I turn what it produced into normalised findings?

Everything else -- starting containers, streaming logs, enforcing the budget,
recording digests -- lives in ``BaseDriver.run`` so that all five tools are subject
to exactly the same treatment. A benchmark where each driver implements its own stop
condition is a benchmark where the differences between tools include the harness.

Budget arithmetic: a run may cover several applications, which means several
invocations. The remaining wall clock is divided evenly among the invocations still
to come, and time unused by one is inherited by the next. That is recorded per
invocation, so nobody has to trust the division.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from .budget import Budget, BudgetWatch, StopReason
from .config import AppSpec, Credentials, ToolSpec
from .findings import NormaliseResult
from .login import Session

log = logging.getLogger("bench.runners.driver")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class Invocation:
    """One container start: what to run, where its output lands."""

    name: str  # usually the app key; becomes part of the container name
    args: list[str]
    app: str | None = None
    image: str | None = None  # None -> the tool's configured image
    entrypoint: str | None = None
    user: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    # (host, container) pairs. The run directory is mounted by default.
    volumes: list[tuple[str, str]] = field(default_factory=list)
    extra_flags: list[str] = field(default_factory=list)
    # Files the tool is expected to write, relative to the run's raw directory.
    # Their absence after the run is reported: an empty findings list because the
    # report was never written must not look like "the tool found nothing".
    artifacts: list[str] = field(default_factory=list)
    notes: str | None = None


@dataclass
class InvocationResult:
    name: str
    app: str | None
    argv: list[str]
    image: str
    image_digest: str | None
    container_id: str | None
    started_at: str
    ended_at: str | None = None
    elapsed_s: float = 0.0
    exit_code: int | None = None
    stop_reason: str = StopReason.ERROR.value
    requests_at_start: int = 0
    requests_at_end: int = 0
    budget_share_s: float | None = None
    artifacts_present: dict[str, bool] = field(default_factory=dict)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = dict(self.__dict__)
        data["requests"] = self.requests_at_end - self.requests_at_start
        return data


@dataclass
class RunContext:
    """Everything a driver is allowed to know about the run."""

    run_id: str
    tool: ToolSpec
    profile: str
    apps: list[AppSpec]
    budget: Budget
    run_dir: Path
    docker: Any
    network: str = "bench-public"
    credentials: dict[str, Credentials] = field(default_factory=dict)
    sessions: dict[str, Session] = field(default_factory=dict)
    # Returns the number of requests the *targets* have seen so far in this run.
    # Injected rather than imported so tests can drive the budget deterministically.
    request_meter: Callable[[], int] = lambda: 0
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    allow_pull: bool = True
    keep_containers: bool = False
    # Root the `build.context` paths in tools.yaml are relative to.
    repo_root: Path | None = None
    # Driver-specific switches from the command line (e.g. the generic driver's
    # findings file). Kept as a bag so adding a driver never changes this class.
    options: dict[str, Any] = field(default_factory=dict)

    # -- run directory layout ---------------------------------------------------
    # results/runs/<run_id>/
    #   run.json          the record (written by the orchestrator)
    #   raw/              tool reports, verbatim
    #   logs/             container stdout/stderr
    #   conf/             generated plans/config, kept as evidence of what was run
    #   findings.json     normalised
    @property
    def raw_dir(self) -> Path:
        return self.run_dir / "raw"

    @property
    def log_dir(self) -> Path:
        return self.run_dir / "logs"

    @property
    def conf_dir(self) -> Path:
        return self.run_dir / "conf"

    def ensure_dirs(self) -> None:
        for path in (self.raw_dir, self.log_dir, self.conf_dir):
            path.mkdir(parents=True, exist_ok=True)
        # Scanner images run as assorted uids (ZAP is 1000, nuclei root, wapiti
        # 1001...). The bind mount must be writable by all of them or the tool
        # silently produces no report, which reads as "found nothing".
        for path in (self.raw_dir, self.conf_dir, self.log_dir):
            try:
                path.chmod(0o777)
            except OSError:  # e.g. a read-only or exotic filesystem; report writes will fail loudly
                log.warning("could not chmod 0777 %s; tool containers may fail to write", path)

    def session_for(self, app: str) -> Session | None:
        return self.sessions.get(app)

    def creds_for(self, app: str) -> Credentials | None:
        return self.credentials.get(app)


class Driver(Protocol):
    key: str

    def run(self, ctx: RunContext) -> list[InvocationResult]: ...

    def normalise(self, raw_dir: Path, **kwargs: Any) -> NormaliseResult: ...


class BaseDriver:
    """Shared container lifecycle. Subclasses implement ``plan`` and ``normalise``."""

    key: str = "base"
    # Where the run directory is mounted inside the tool container.
    container_workdir: str = "/bench"
    default_user: str | None = None
    default_entrypoint: str | None = None

    # -- to implement -----------------------------------------------------------

    def plan(self, ctx: RunContext) -> list[Invocation]:
        raise NotImplementedError

    def normalise(self, raw_dir: Path, **kwargs: Any) -> NormaliseResult:
        raise NotImplementedError

    # argv that makes the image print its version, and the entrypoint to use for it.
    version_command: list[str] | None = None
    version_entrypoint: str | None = None

    def version(self, ctx: RunContext) -> str | None:
        """Tool version string, for the run record.

        Failure is not fatal -- the image digest is the identity that matters -- but
        the human-readable version is what ends up in the published table's
        footnotes, and "we could not determine it" is itself worth recording.
        """
        if not self.version_command:
            return None
        try:
            res = ctx.docker.run_capture(
                ctx.tool.image_ref,
                self.version_command,
                entrypoint=self.version_entrypoint,
                allow_pull=ctx.allow_pull,
                build_spec=ctx.tool.build,
                context_root=ctx.repo_root,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("%s: version probe failed: %s", self.key, exc)
            return None
        text = (res.stdout or "") + (res.stderr or "")
        return self.parse_version(text)

    def parse_version(self, text: str) -> str | None:
        """First non-empty line of the version output, cleaned of ANSI noise.

        Several of these tools print a colourful banner; the run record wants a
        string a human can compare, not escape codes.
        """
        import re as _re

        clean = _re.sub(r"\x1b\[[0-9;]*m", "", text)
        for line in clean.splitlines():
            line = line.strip()
            if line and not line.startswith(("_", "\\", "/", "|", "-")):
                return line[:200]
        return None

    # -- the uniform part -------------------------------------------------------

    def run(self, ctx: RunContext) -> list[InvocationResult]:
        ctx.ensure_dirs()
        invocations = self.plan(ctx)
        if not invocations:
            log.warning("%s: nothing to run", self.key)
            return []

        watch = BudgetWatch(ctx.budget, clock=ctx.clock)
        results: list[InvocationResult] = []
        for index, inv in enumerate(invocations):
            remaining_invocations = len(invocations) - index
            share = None
            if ctx.budget.wall_clock_s is not None:
                share = max(1.0, (watch.remaining_s() or 0.0) / remaining_invocations)
            result = self._run_one(ctx, inv, watch, share)
            results.append(result)
            state = watch.check(ctx.request_meter())
            if state.reason in (StopReason.BUDGET_WALL_CLOCK, StopReason.BUDGET_REQUESTS):
                # The global budget is spent: the remaining applications are not
                # scanned, and that is recorded rather than silently reported as
                # "no findings on those targets".
                for skipped in invocations[index + 1 :]:
                    results.append(
                        InvocationResult(
                            name=skipped.name,
                            app=skipped.app,
                            argv=[],
                            image=inv.image or ctx.tool.image_ref,
                            image_digest=None,
                            container_id=None,
                            started_at=_now(),
                            ended_at=_now(),
                            stop_reason=state.reason.value,
                            error="not started: run budget exhausted by earlier targets",
                        )
                    )
                break
        return results

    def _run_one(
        self,
        ctx: RunContext,
        inv: Invocation,
        watch: BudgetWatch,
        share_s: float | None,
    ) -> InvocationResult:
        image = inv.image or ctx.tool.image_ref
        container_name = f"bench-{self.key}-{inv.name}-{ctx.run_id[:8]}"
        log_path = ctx.log_dir / f"{inv.name}.log"
        volumes = [(str(ctx.run_dir), self.container_workdir), *inv.volumes]
        requests_at_start = _safe_meter(ctx)

        result = InvocationResult(
            name=inv.name,
            app=inv.app,
            argv=[],
            image=image,
            image_digest=None,
            container_id=None,
            started_at=_now(),
            requests_at_start=requests_at_start,
            budget_share_s=share_s,
        )

        try:
            handle = ctx.docker.run_detached(
                image,
                inv.args,
                name=container_name,
                network=ctx.network,
                volumes=volumes,
                env=inv.env,
                entrypoint=inv.entrypoint or self.default_entrypoint,
                user=inv.user or self.default_user,
                extra_flags=inv.extra_flags,
                log_path=log_path,
                allow_pull=ctx.allow_pull,
                build_spec=ctx.tool.build,
                context_root=ctx.repo_root,
            )
        except Exception as exc:  # noqa: BLE001 - one target failing must not lose the others
            result.error = f"could not start container: {exc}"
            result.ended_at = _now()
            log.error("%s: %s", self.key, result.error)
            return result

        result.argv = handle.argv
        result.image_digest = handle.image_digest
        result.container_id = handle.container_id

        local = BudgetWatch(
            Budget(
                wall_clock_s=int(share_s) if share_s else None,
                max_requests=ctx.budget.max_requests,
                grace_s=ctx.budget.grace_s,
                poll_interval_s=ctx.budget.poll_interval_s,
            ),
            clock=ctx.clock,
        )
        stop_reason = StopReason.ERROR
        try:
            while True:
                running = ctx.docker.is_running(handle.container_id)
                requests = _safe_meter(ctx)
                # The global watch decides too: a per-invocation share must never
                # let the run as a whole overrun its declared budget.
                state = local.check(requests, tool_exited=not running)
                global_state = watch.check(requests)
                reason = state.reason or global_state.reason
                if reason is not None:
                    stop_reason = reason
                    break
                ctx.sleep(ctx.budget.poll_interval_s)
        except KeyboardInterrupt:
            stop_reason = StopReason.INTERRUPTED
        finally:
            if stop_reason is not StopReason.COMPLETED:
                # Graceful stop: ZAP and Arachni write their report on shutdown.
                try:
                    ctx.docker.stop(handle.container_id, grace=ctx.budget.grace_s)
                except Exception as exc:  # noqa: BLE001
                    log.warning("stopping %s failed: %s", handle.container_id, exc)
            result.exit_code = ctx.docker.exit_code(handle.container_id)
            ctx.docker.close_logs(handle)
            if not ctx.keep_containers:
                try:
                    ctx.docker.rm(handle.container_id)
                except Exception as exc:  # noqa: BLE001
                    log.warning("removing %s failed: %s", handle.container_id, exc)

        result.stop_reason = stop_reason.value
        result.ended_at = _now()
        result.elapsed_s = round(local.elapsed, 2)
        result.requests_at_end = _safe_meter(ctx)
        result.artifacts_present = {
            name: (ctx.raw_dir / name).exists() for name in inv.artifacts
        }
        missing = [n for n, present in result.artifacts_present.items() if not present]
        if missing:
            # Loud, because "no report" and "no findings" are indistinguishable in
            # the results file and mean completely different things.
            log.error(
                "%s/%s produced no %s -- treat this run as failed, not as a clean scan",
                self.key,
                inv.name,
                ", ".join(missing),
            )
        return result

    # -- helpers for subclasses -------------------------------------------------

    def in_container(self, ctx: RunContext, path: Path) -> str:
        """Translate a host path inside the run directory to its container path."""
        return f"{self.container_workdir}/{path.relative_to(ctx.run_dir).as_posix()}"

    def raw_path(self, ctx: RunContext, filename: str) -> Path:
        return ctx.raw_dir / filename

    def scope_hosts(self, ctx: RunContext) -> list[str]:
        return [app.host for app in ctx.apps]


def _safe_meter(ctx: RunContext) -> int:
    """The meter talks to the collector; a transient failure must not kill the run."""
    try:
        return int(ctx.request_meter())
    except Exception as exc:  # noqa: BLE001
        log.warning("request meter unavailable (%s); budget falls back to wall clock", exc)
        return 0


def write_text(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    try:
        path.chmod(0o666)
    except OSError:
        pass
    return path

