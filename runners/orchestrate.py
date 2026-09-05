#!/usr/bin/env python3
"""One benchmark run, end to end, reproducibly.

    reset targets -> verify the reset -> log in -> open the run -> start the tool
    with a recorded budget -> stream its logs -> stop it -> close the run ->
    normalise its findings -> score.

Usage:

    python runners/orchestrate.py --tool zap --profile full --app shopfront \\
        --budget 45m --max-requests 20000

    python runners/orchestrate.py --tool generic --app shopfront --attach \\
        --budget 2h --findings /tmp/vendor-export.sarif

    python runners/orchestrate.py --tool nuclei --app blog --dry-run

The four properties this file exists to guarantee, none of which are optional:

**The target is provably clean before the run opens.** Half the corpus is stateful.
If ZAP stores an XSS payload and the app is not rebuilt, nuclei is credited with
ZAP's finding on the next run and nothing in the results says so. So a reset is a
restart plus a re-seed plus four assertions (see _lib/reset.py), and a reset that
cannot be verified aborts the run rather than producing a number.

**The budget is explicit, enforced and recorded.** A table where one tool got ten
minutes and another six hours measures the operator's patience. The budget is a
command-line argument, it is enforced on wall clock and on requests-seen-by-the-
target, and it is written into run.json next to the findings so a reader can see
what each column cost. When a tool is cut off, ``stop_reason`` says so: its result
is a lower bound, not a score.

**Every number is re-runnable from the record alone.** run.json carries the image
digest (not just the tag), the exact argv of every container, the tool version, the
budget, the reset evidence, the harness commit and both timestamps.

**Authenticated scans are actually authenticated.** Each tool is given credentials
in its own dialect, the harness verifies the session before the run starts, and it
refuses to proceed with an unverified session rather than publishing an anonymous
scan as an authenticated one.

Exit codes: 0 success; 1 usage or configuration error; 2 target reset could not be
verified; 3 authentication failed; 4 the tool produced no report; 5 scoring failed.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

if __package__ in (None, ""):  # allow `python runners/orchestrate.py`
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from runners._lib.budget import Budget, StopReason
from runners._lib.collector import CollectorClient
from runners._lib.config import REPO_ROOT, RUNNERS_DIR, BenchConfig, ConfigError
from runners._lib.dockerctl import DockerClient
from runners._lib.driver import RunContext
from runners._lib.findings import NormaliseResult
from runners._lib.internal_http import DirectHttp, ExecHttp, HttpError
from runners._lib.login import LoginError, Session, establish
from runners._lib.normalise import CweTable, default_table
from runners._lib.reset import ResetError, TargetResetter, reset_targets

log = logging.getLogger("bench.runners")

RECORD_SCHEMA = "ptaas-bench/run-record/1"

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_RESET = 2
EXIT_AUTH = 3
EXIT_NO_REPORT = 4
EXIT_SCORE = 5


# --------------------------------------------------------------------------------
# driver registry
# --------------------------------------------------------------------------------


def load_driver(tool: str):
    """Import ``runners/<tool>/`` and return its DRIVER.

    Import by convention rather than a registry table: adding a tool means adding a
    directory, and a directory that is not importable fails here rather than half
    way through a two-hour run.
    """
    import importlib

    try:
        module = importlib.import_module(f"runners.{tool}")
    except ModuleNotFoundError as exc:
        available = sorted(
            p.name for p in RUNNERS_DIR.iterdir() if (p / "__init__.py").exists() and p.name != "_lib"
        )
        raise ConfigError(f"no driver for tool {tool!r}. Available: {available}") from exc
    driver = getattr(module, "DRIVER", None)
    if driver is None:
        raise ConfigError(f"runners/{tool} does not export DRIVER")
    return driver


# --------------------------------------------------------------------------------
# provenance
# --------------------------------------------------------------------------------


def harness_commit() -> str | None:
    """The repository commit, read from .git without shelling out to git.

    A published number has to be traceable to the harness that produced it: the
    same tool, the same budget and a different normaliser is a different result.
    """
    head = REPO_ROOT / ".git" / "HEAD"
    try:
        content = head.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if content.startswith("ref:"):
        ref = content.split(" ", 1)[1].strip()
        ref_path = REPO_ROOT / ".git" / ref
        try:
            return ref_path.read_text(encoding="utf-8").strip()
        except OSError:
            # Packed refs: not worth parsing, and its absence is not fatal.
            return None
    return content or None


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# --------------------------------------------------------------------------------
# the run
# --------------------------------------------------------------------------------


class Orchestrator:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.config = BenchConfig.load(
            apps_path=args.apps_file,
            tools_path=args.tools_file,
            credentials_path=args.credentials,
        )
        self.docker = DockerClient(
            compose_file=self._compose_file(),
            project=self.config.compose_project,
            dry_run=args.dry_run,
        )
        http = (
            DirectHttp()
            if args.collector_transport == "direct"
            else ExecHttp(self.docker, self.config.collector_service)
        )
        self.http = http
        self.collector = CollectorClient(http, args.collector_url or self.config.collector_url)
        self.apps = self.config.select_apps(args.app)
        self.tool = self._tool_spec()
        self.driver = load_driver(args.tool)
        self.budget = Budget.parse(
            wall_clock=args.budget,
            max_requests=args.max_requests,
            grace=args.grace,
            poll_interval_s=args.poll_interval,
        )
        # Loaded once: the mapping table version goes into the run record, because a
        # results file is only comparable with another one produced by the same table.
        self.table = CweTable.load(args.cwe_map) if args.cwe_map else default_table()
        self.record: dict[str, Any] = {}

    def _compose_file(self) -> Path:
        path = self.config.compose_file or (REPO_ROOT / "docker-compose.yml")
        return path if path.is_absolute() else (REPO_ROOT / path)

    def _tool_spec(self):
        try:
            return self.config.tools[self.args.tool]
        except KeyError:
            raise ConfigError(
                f"tool {self.args.tool!r} is not in tools.yaml (known: {sorted(self.config.tools)})"
            ) from None

    # -- steps ------------------------------------------------------------------

    def run(self) -> int:
        profile = self.args.profile or self.tool.default_profile
        if self.tool.profiles and profile not in self.tool.profiles:
            log.error("profile %r not in %s for %s", profile, self.tool.profiles, self.tool.key)
            return EXIT_USAGE

        if self.args.dry_run:
            return self._dry_run(profile)

        if self.args.import_into:
            # The scan already happened during a run we opened earlier; opening a new
            # one would attach the vendor's claims to a run with no events in it, and
            # every one of them would score as unreachable.
            return self._import_into(profile, self.args.import_into)

        started_at = now_iso()
        started_mono = time.monotonic()

        # Fail before touching the targets: a run that cannot be opened is a reset
        # spent for nothing, and a scan with no run open is unattributable traffic.
        if not self.collector.healthz():
            log.error(
                "the collector is not answering at %s over the %s transport. It lives on "
                "bench-internal with no published port, so `direct` only works from inside "
                "that network.",
                self.collector.base_url,
                self.args.collector_transport,
            )
            return EXIT_USAGE

        # 1. Reset, and prove it. Before anything else: an unclean target makes
        #    every subsequent step meaningless.
        try:
            resets = self._reset()
        except ResetError as exc:
            log.error("%s", exc)
            return EXIT_RESET

        # 2. Log in, and prove that too. Deliberately before the run is opened, so
        #    the harness's own login traffic is never attributed to the tool.
        try:
            sessions = self._login()
        except LoginError as exc:
            log.error("%s", exc)
            return EXIT_AUTH

        # 3. Identify what is about to run, while nothing is being measured.
        version = None
        if not self.args.skip_version_probe:
            version = self._probe_version(profile)

        # 4. Open the run. From here on, every request the targets see belongs to
        #    this tool.
        run = self.collector.open_run(
            tool=self.tool.key,
            tool_version=version,
            profile=profile,
            targets=[a.key for a in self.apps],
            notes=self.args.notes,
            force=self.args.force,
        )
        log.info("collector run %s open (%s/%s)", run.run_id, self.tool.key, profile)

        run_dir = Path(self.args.results_dir or self.config.results_dir) / run.run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        ctx = RunContext(
            run_id=run.run_id,
            tool=self.tool,
            profile=profile,
            apps=self.apps,
            budget=self.budget,
            run_dir=run_dir,
            docker=self.docker,
            network=self.config.network,
            credentials={a.key: c for a in self.apps if (c := self.config.creds_for(a.key))},
            sessions=sessions,
            request_meter=self.collector.event_count,
            allow_pull=not self.args.no_pull,
            keep_containers=self.args.keep_containers,
            repo_root=REPO_ROOT,
            options=self._driver_options(),
        )

        # 5. Run it. Any failure here still has to leave a record: a run that
        #    crashed and a run that found nothing are different results, and only
        #    the record can tell them apart afterwards.
        results = []
        run_error: str | None = None
        try:
            results = self.driver.run(ctx)
        except KeyboardInterrupt:
            run_error = "interrupted by the operator"
            log.warning("interrupted; closing the run so the next tool starts clean")
        except Exception as exc:  # noqa: BLE001
            run_error = f"{type(exc).__name__}: {exc}"
            log.exception("the tool run failed")
        finally:
            # 6. Close the run *whatever happened*. An abandoned active run makes the
            #    next tool's events land in this one's bucket.
            closed = self.collector.close_run(run.run_id)
            requests_observed = self.collector.count_http_requests(run.run_id)

        # 7. Normalise, and keep the audit trail of what could not be mapped.
        normalised = self._normalise(ctx)

        self.record = self._build_record(
            run_id=run.run_id,
            profile=profile,
            version=version,
            started_at=started_at,
            duration_s=round(time.monotonic() - started_mono, 2),
            resets=resets,
            sessions=sessions,
            results=results,
            closed=closed,
            requests_observed=requests_observed,
            normalised=normalised,
            run_error=run_error,
        )

        # 8. Write the record, then score, then record the scoring outcome. In that
        #    order: `bench score` reads run.json (budget, tool, targets), and the
        #    record has to survive a scoring failure rather than disappear with it.
        self._write_record(run_dir)
        self.record["score"] = self._score(run_dir)
        self._write_record(run_dir)
        score_status = self.record["score"]

        log.info(
            "run %s: %d findings (%d unmapped), %d requests, stop=%s -> %s",
            run.run_id,
            len(normalised.findings),
            len(normalised.unmapped),
            requests_observed,
            self.record["stop_reason"],
            run_dir,
        )

        if run_error is not None:
            return EXIT_NO_REPORT
        if results and not any(
            all(r.artifacts_present.values()) for r in results if r.artifacts_present
        ):
            log.error("no tool report was produced: this run is a failure, not a clean scan")
            return EXIT_NO_REPORT
        if score_status.get("returncode") not in (0, None):
            return EXIT_SCORE
        return EXIT_OK

    # -- individual steps -------------------------------------------------------

    def _reset(self) -> list[dict[str, Any]]:
        if self.args.skip_reset:
            # Supported for debugging a driver, never for a published run, and it is
            # recorded so a results file produced this way is identifiable.
            log.warning(
                "--skip-reset: targets keep the previous tool's state. Any stored-payload "
                "finding in this run may belong to another scanner."
            )
            return [{"app": a.key, "ok": False, "checks": [], "skipped": True} for a in self.apps]

        resetter = TargetResetter(self.docker, self.http, health_timeout_s=self.args.health_timeout)
        previous = self._previous_seed_ids()
        outcomes = reset_targets(resetter, self.apps, previous_seed_ids=previous, strict=True)
        for outcome in outcomes:
            log.info("reset %s ok in %.1fs (seed %s)", outcome.app, outcome.duration_s, outcome.seed_id)
        return [o.to_dict() for o in outcomes]

    def _previous_seed_ids(self) -> dict[str, str]:
        """Seed ids from the most recent run, so a repeated seed id is detectable.

        A seed endpoint that returns the same id twice has not rebuilt anything; that
        is exactly the silent failure this whole module exists to catch.
        """
        results_dir = Path(self.args.results_dir or self.config.results_dir)
        if not results_dir.exists():
            return {}
        records = sorted(results_dir.glob("*/run.json"), key=lambda p: p.stat().st_mtime)
        if not records:
            return {}
        try:
            previous = json.loads(records[-1].read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return {
            entry.get("app"): entry.get("seed_id")
            for entry in previous.get("reset", [])
            if entry.get("app") and entry.get("seed_id")
        }

    def _login(self) -> dict[str, Session]:
        sessions: dict[str, Session] = {}
        for app in self.apps:
            creds = self.config.creds_for(app.key)
            if creds is None:
                # Not an error -- part of the corpus is deliberately anonymous -- but
                # never silent, because "the tool found nothing behind the login" and
                # "we never logged in" look identical in a results table.
                log.warning("%s: no credentials configured; scanning anonymously", app.key)
                continue
            sessions[app.key] = establish(self.http, creds, strict=not self.args.allow_unverified_auth)
            log.info("%s: session established (%s)", app.key, sessions[app.key].detail)
        return sessions

    def _probe_version(self, profile: str) -> str | None:
        ctx = RunContext(
            run_id="version-probe",
            tool=self.tool,
            profile=profile,
            apps=self.apps,
            budget=self.budget,
            run_dir=Path(self.args.results_dir or self.config.results_dir) / "_probe",
            docker=self.docker,
            allow_pull=not self.args.no_pull,
            repo_root=REPO_ROOT,
        )
        version = self.driver.version(ctx)
        if version is None:
            log.warning("could not determine %s version; the image digest is the record", self.tool.key)
        return version

    def _driver_options(self) -> dict[str, Any]:
        """Driver switches from the command line.

        Unset options are *removed* rather than passed as None: the drivers read
        them with ``ctx.options.get(key, <default>)``, and a present-but-None value
        would silently defeat the default -- which is how `-l None` ends up on a
        real command line.
        """
        options = {
            "attach": self.args.attach,
            "findings_file": self.args.findings,
            "rate_limit": self.args.rate_limit,
            "wordlist": self.args.wordlist,
            "offline": self.args.offline,
        }
        return {k: v for k, v in options.items() if v is not None}

    def _normalise(self, ctx: RunContext) -> NormaliseResult:
        result = self.driver.normalise(ctx.raw_dir, table=self.table)
        result.write(ctx.run_dir / "findings.json", ctx.run_dir / "unmapped.json")
        if result.unmapped:
            log.warning(
                "%d finding(s) could not be mapped to a CWE and were emitted as null; "
                "see %s",
                len(result.unmapped),
                ctx.run_dir / "unmapped.json",
            )
        return result

    def _score(self, run_dir: Path) -> dict[str, Any]:
        if self.args.no_score:
            return {"skipped": "disabled with --no-score"}
        command = self.args.score_command or ["bench", "score", "--run", str(run_dir)]
        if shutil.which(command[0]) is None:
            # benchctl is a separate component; a harness run is still valid without
            # it, and pretending otherwise would make the runners untestable alone.
            return {"skipped": f"{command[0]} not on PATH", "command": command}
        proc = subprocess.run(command, capture_output=True, text=True)
        (run_dir / "logs").mkdir(parents=True, exist_ok=True)
        (run_dir / "logs" / "score.log").write_text(
            (proc.stdout or "") + (proc.stderr or ""), encoding="utf-8"
        )
        return {"command": command, "returncode": proc.returncode}

    # -- the record -------------------------------------------------------------

    def _build_record(self, **kw: Any) -> dict[str, Any]:
        results = kw["results"]
        stop_reasons = [r.stop_reason for r in results]
        stop_reason = StopReason.ERROR.value if kw.get("run_error") else StopReason.COMPLETED.value
        for candidate in (
            StopReason.BUDGET_WALL_CLOCK.value,
            StopReason.BUDGET_REQUESTS.value,
            StopReason.ERROR.value,
            StopReason.INTERRUPTED.value,
        ):
            if candidate in stop_reasons:
                stop_reason = candidate
                break

        return {
            "schema": RECORD_SCHEMA,
            "run_id": kw["run_id"],
            "tool": self.tool.key,
            "tool_version": kw["version"],
            "profile": kw["profile"],
            "image": self.tool.image_ref,
            "image_digest": next(
                (r.image_digest for r in results if r.image_digest), None
            ),
            "targets": [a.key for a in self.apps],
            "budget": self.budget.to_dict(),
            "budget_note": (
                "The tool was stopped by the budget: its findings are a lower bound."
                if StopReason(stop_reason).exhausted
                else "The tool finished within its budget."
            ),
            "started_at": kw["started_at"],
            "ended_at": now_iso(),
            "duration_s": kw["duration_s"],
            "stop_reason": stop_reason,
            "error": kw.get("run_error"),
            "requests_observed": kw["requests_observed"],
            "collector": {
                "run_id": kw["closed"].run_id,
                "started_at": kw["closed"].started_at,
                "closed_at": kw["closed"].closed_at,
                "event_count": kw["closed"].event_count,
            },
            "harness": {
                "commit": harness_commit(),
                "python": platform.python_version(),
                "host": platform.platform(),
                "argv": sys.argv,
                "user": os.environ.get("USER"),
            },
            "reset": kw["resets"],
            "auth": {app: session.to_dict() for app, session in kw["sessions"].items()},
            "invocations": [r.to_dict() for r in results],
            "normalisation": {
                "cwe_map_version": self.table.version,
                "findings": len(kw["normalised"].findings),
                "unmapped": len(kw["normalised"].unmapped),
                "findings_file": "findings.json",
                "unmapped_file": "unmapped.json",
            },
        }

    def _write_record(self, run_dir: Path) -> None:
        (run_dir / "run.json").write_text(
            json.dumps(self.record, indent=2, sort_keys=False, default=str) + "\n",
            encoding="utf-8",
        )

    # -- import into an existing run ---------------------------------------------

    def _import_into(self, profile: str, run_id: str) -> int:
        """Normalise a findings file against a run that has already been collected."""
        run_dir = Path(self.args.results_dir or self.config.results_dir) / run_id
        if not run_dir.exists():
            log.error("no run directory at %s", run_dir)
            return EXIT_USAGE
        ctx = RunContext(
            run_id=run_id,
            tool=self.tool,
            profile=profile,
            apps=self.apps,
            budget=self.budget,
            run_dir=run_dir,
            docker=self.docker,
            network=self.config.network,
            repo_root=REPO_ROOT,
            options=self._driver_options(),
        )
        results = self.driver.run(ctx)
        normalised = self._normalise(ctx)

        record_path = run_dir / "run.json"
        record: dict[str, Any] = {}
        if record_path.exists():
            try:
                record = json.loads(record_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                record = {}
        # The original record is not overwritten: what is added is the fact that a
        # findings file was ingested afterwards, and by whom.
        record.setdefault("schema", RECORD_SCHEMA)
        record.setdefault("run_id", run_id)
        record["ingested"] = {
            "at": now_iso(),
            "tool": self.tool.key,
            "profile": profile,
            "source": str(self.args.findings) if self.args.findings else None,
            "findings": len(normalised.findings),
            "unmapped": len(normalised.unmapped),
            "cwe_map_version": self.table.version,
            "invocations": [r.to_dict() for r in results],
        }
        self.record = record
        self._write_record(run_dir)
        self.record["score"] = self._score(run_dir)
        self._write_record(run_dir)
        log.info("ingested %d findings into run %s", len(normalised.findings), run_id)
        return EXIT_OK if not results or results[0].error is None else EXIT_NO_REPORT

    # -- dry run ----------------------------------------------------------------

    def _dry_run(self, profile: str) -> int:
        """Print exactly what would be run, without touching docker or the collector.

        This is what makes a driver reviewable: the generated ZAP plan and every
        container argv end up on disk and can be read before anything is started.
        """
        run_dir = Path(self.args.results_dir or self.config.results_dir) / f"dryrun-{int(time.time())}"
        ctx = RunContext(
            run_id=run_dir.name,
            tool=self.tool,
            profile=profile,
            apps=self.apps,
            budget=self.budget,
            run_dir=run_dir,
            docker=self.docker,
            network=self.config.network,
            credentials={a.key: c for a in self.apps if (c := self.config.creds_for(a.key))},
            sessions={},
            repo_root=REPO_ROOT,
            options=self._driver_options(),
        )
        ctx.ensure_dirs()
        invocations = self.driver.plan(ctx)
        print(f"# {self.tool.key}/{profile}  image={self.tool.image_ref}")
        print(f"# budget: {self.budget.describe()}")
        print(f"# generated config: {ctx.conf_dir}")
        for inv in invocations:
            argv = [
                "docker", "run", "--rm", "--network", self.config.network,
                "-v", f"{run_dir}:{self.driver.container_workdir}",
            ]
            for key in inv.env:
                argv += ["-e", f"{key}=<from environment>"]
            if inv.entrypoint or self.driver.default_entrypoint:
                argv += ["--entrypoint", inv.entrypoint or self.driver.default_entrypoint]
            argv += [self.tool.image_ref, *inv.args]
            import shlex

            print(f"\n# target: {inv.app}")
            print(shlex.join(argv))
        return EXIT_OK


# --------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="orchestrate.py",
        description="Run one benchmark scan end to end and record it reproducibly.",
    )
    parser.add_argument("--tool", required=True, help="Tool key, i.e. a directory under runners/")
    parser.add_argument("--profile", help="Scan profile (see tools.yaml)")
    parser.add_argument(
        "--app",
        action="append",
        help="Target app key; repeatable. Default: every app in apps.yaml. "
        "With several, the budget is split evenly and the split is recorded.",
    )

    budget = parser.add_argument_group("budget (recorded in run.json)")
    budget.add_argument("--budget", default="1h", help="Wall clock, e.g. 45m, 2h, 5400. Default 1h.")
    budget.add_argument(
        "--max-requests",
        type=int,
        help="Stop once the targets have seen this many requests from the tool.",
    )
    budget.add_argument("--grace", default="30", help="Seconds a stopped tool gets to write its report.")
    budget.add_argument("--poll-interval", type=float, default=5.0, help="Budget check interval.")

    io = parser.add_argument_group("configuration")
    io.add_argument("--apps-file", type=Path, help="Default: runners/apps.yaml")
    io.add_argument("--tools-file", type=Path, help="Default: runners/tools.yaml")
    io.add_argument("--credentials", type=Path, help="Default: runners/credentials.yaml")
    io.add_argument("--cwe-map", type=Path, help="Default: runners/_lib/cwe_map.yaml")
    io.add_argument("--results-dir", type=Path, help="Default: results/runs")
    io.add_argument("--collector-url", help="Default: from apps.yaml")
    io.add_argument(
        "--collector-transport",
        choices=["exec", "direct"],
        default="exec",
        help="exec (default) reaches the collector through its own container, which "
        "keeps bench-internal unreachable from the host. direct assumes you are "
        "already on that network.",
    )

    behaviour = parser.add_argument_group("behaviour")
    behaviour.add_argument("--notes", help="Free text stored on the collector run")
    behaviour.add_argument("--force", action="store_true", help="Close any run left active")
    behaviour.add_argument(
        "--skip-reset",
        action="store_true",
        help="DEBUG ONLY. Skips the target reset; the run is marked as such and must "
        "not be published -- stored findings may belong to the previous tool.",
    )
    behaviour.add_argument(
        "--allow-unverified-auth",
        action="store_true",
        help="DEBUG ONLY. Proceed when the login could not be verified.",
    )
    behaviour.add_argument("--health-timeout", type=float, default=120.0)
    behaviour.add_argument("--no-pull", action="store_true", help="Fail instead of pulling an image")
    behaviour.add_argument("--keep-containers", action="store_true", help="Do not docker rm afterwards")
    behaviour.add_argument("--skip-version-probe", action="store_true")
    behaviour.add_argument("--no-score", action="store_true")
    behaviour.add_argument(
        "--score-command", nargs="+", help="Default: bench score --run <run dir>"
    )
    behaviour.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate the tool configuration and print the container command lines "
        "without starting anything.",
    )
    behaviour.add_argument("-v", "--verbose", action="store_true")

    driver_opts = parser.add_argument_group("driver options")
    driver_opts.add_argument("--findings", type=Path, help="generic: the vendor's export to ingest")
    driver_opts.add_argument(
        "--import-into",
        metavar="RUN_ID",
        help="generic: normalise --findings against an existing run instead of "
        "opening a new one (the scan already happened during that run)",
    )
    driver_opts.add_argument(
        "--attach",
        action="store_true",
        help="generic: hold the run open for the budget while the vendor scans",
    )
    driver_opts.add_argument("--rate-limit", type=int, help="nuclei/skipfish: requests per second")
    driver_opts.add_argument("--wordlist", help="skipfish: dictionary path inside the image")
    driver_opts.add_argument("--offline", action="store_true", help="nuclei: disable interactsh")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    try:
        return Orchestrator(args).run()
    except ConfigError as exc:
        log.error("%s", exc)
        return EXIT_USAGE
    except HttpError as exc:
        # Most often: a previous run was left active on the collector.
        log.error("collector: %s", exc)
        return EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())
