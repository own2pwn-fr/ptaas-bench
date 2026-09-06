"""Generic driver: benchmark a tool we have no driver for.

A commercial PTaaS product cannot be started with ``docker run``: it is a service,
often driven by a human, sometimes an agent with its own scheduling. Refusing to
benchmark those would leave exactly the class of tool this project exists to compare
out of the comparison table, so instead the harness offers them the same deal as
everyone else:

    the platform opens a run and holds it open for a declared budget, the vendor
    scans the same targets over bench-public, then hands us their findings export
    (JSON or SARIF). The ground truth still comes from our collector, not from them.

That last point is what keeps this honest. The vendor supplies *claims*; reach,
exercise and trigger still come from the instrumented targets, so a vendor cannot
score by asserting. Their file only decides which claims are matched against the
catalog and which are false positives.

Two modes:

* ``--attach``      -- hold the run open for the budget (or until a sentinel file
  appears in the run directory), then ingest. Use while the vendor scans live.
* ``--import-into <run id>`` -- the scan already happened during a run we opened
  earlier; ingest the export against *that* run. Opening a fresh run instead would
  attach the vendor's claims to a run with no events, and every one of them would
  score as unreachable.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .._lib.budget import BudgetWatch, StopReason
from .._lib.driver import BaseDriver, Invocation, InvocationResult, RunContext
from .._lib.findings import NormaliseResult
from .._lib.normalise import CweTable, normalise_generic

# Dropping this file into the run directory ends an --attach wait early, so the
# operator does not have to sit out the whole budget when the vendor finishes first.
SENTINEL = "VENDOR-DONE"

RAW_NAME = "generic-findings.json"


class GenericDriver(BaseDriver):
    key = "generic"

    def plan(self, ctx: RunContext) -> list[Invocation]:
        # No container: the tool under test is not ours to start.
        return []

    def run(self, ctx: RunContext) -> list[InvocationResult]:
        ctx.ensure_dirs()
        started = _now()
        watch = BudgetWatch(ctx.budget, clock=ctx.clock)
        attach = bool(ctx.options.get("attach"))
        findings_path = ctx.options.get("findings_file")

        reason = StopReason.COMPLETED
        if attach:
            reason = self._wait(ctx, watch)

        error = None
        if findings_path:
            src = Path(findings_path)
            if src.exists():
                shutil.copyfile(src, ctx.raw_dir / RAW_NAME)
            else:
                error = f"findings file not found: {src}"
        else:
            error = "no --findings file supplied; nothing to normalise"

        return [
            InvocationResult(
                name="vendor",
                app=",".join(a.key for a in ctx.apps),
                argv=[],
                image="(no container: externally driven tool)",
                image_digest=None,
                container_id=None,
                started_at=started,
                ended_at=_now(),
                elapsed_s=round(watch.elapsed, 2),
                exit_code=0 if error is None else 1,
                stop_reason=reason.value,
                requests_at_start=0,
                requests_at_end=_meter(ctx),
                artifacts_present={RAW_NAME: (ctx.raw_dir / RAW_NAME).exists()},
                error=error,
            )
        ]

    def performs_active_scanning(self, ctx, invocations):
        # We did not drive this tool and cannot claim what it did. The collector's
        # event stream shows what actually reached the targets.
        return True, "externally driven: mode not observable from here"

    def _wait(self, ctx: RunContext, watch: BudgetWatch) -> StopReason:
        sentinel = ctx.run_dir / SENTINEL
        while True:
            if sentinel.exists():
                return StopReason.COMPLETED
            state = watch.check(_meter(ctx))
            if state.reason is not None:
                return state.reason
            ctx.sleep(ctx.budget.poll_interval_s)

    def normalise(self, raw_dir: Path, table: CweTable | None = None, **_: Any) -> NormaliseResult:
        out = NormaliseResult()
        # Any JSON/SARIF the operator dropped in raw/, not just the canonical name:
        # a vendor that exports one file per application should not need a driver.
        for path in sorted(list(raw_dir.glob("generic-*.json")) + list(raw_dir.glob("*.sarif"))):
            out.extend(normalise_generic(path, table=table))
        return out


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _meter(ctx: RunContext) -> int:
    try:
        return int(ctx.request_meter())
    except Exception:  # noqa: BLE001
        return 0


DRIVER = GenericDriver()
run = DRIVER.run
normalise = DRIVER.normalise
