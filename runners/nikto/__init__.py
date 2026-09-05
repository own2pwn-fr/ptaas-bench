"""nikto driver.

nikto is in the comparison as the floor: it is a signature scanner for known files,
misconfigurations and stale software, it does not crawl, and it does not fuzz
parameters. On the `infra` target it should do well; on the SPA targets it should
find close to nothing. Publishing that contrast is the point -- provided the tool is
given a fair run, which here means three specific things:

* **Pin 2.6.1.** ``ghcr.io/sullo/nikto:latest`` is stale (built 2026-02, labelled
  2.5.0) and the 2.5.0 JSON writer is broken in five separate ways -- it hand-builds
  JSON strings, never sets ``url``, and truncates the opening bracket. 2.6.1 uses a
  real serialiser. Benchmarking a tool through a broken exporter measures the
  exporter.
* **No ``-Tuning``.** It is tempting to disable the DoS tests, but choosing which
  test classes a scanner runs is the harness taking a position on what the tool
  should look for. Default behaviour, and let the result be the result.
* **``-maxtime`` below the budget.** nikto only serialises its report at
  ``report_close``: killed at the deadline, it leaves nothing at all, and "no report"
  is indistinguishable from "no findings" in a results table. So nikto is asked to
  stop early enough to write.

Flag hazards encoded here (nikto parses with ``Getopt::Long`` + ``auto_abbrev``, so
several short flags are ambiguous and abort): always spell out ``-Pause``, ``-id``,
``-maxtime``, ``-Plugins``; ``-h`` is host but ``-H`` is help; ``-o`` is output but
``-O`` is option; and ``-no404`` was removed from the option table in 2.6.x, so
passing it aborts the scan.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .._lib.driver import BaseDriver, Invocation, RunContext
from .._lib.findings import NormaliseResult
from .._lib.normalise import CweTable, normalise_nikto


class NiktoDriver(BaseDriver):
    key = "nikto"
    version_command = ["-Version"]

    def plan(self, ctx: RunContext) -> list[Invocation]:
        share_s = _share_seconds(ctx)
        invocations: list[Invocation] = []
        for app in ctx.apps:
            out_name = f"nikto-{app.key}.json"
            args = [
                "-host",
                app.base_url,
                "-Format",
                "json",
                "-output",
                f"{self.container_workdir}/raw/{out_name}",
                # Never block on a prompt: a benchmark that needs a human to press a
                # key has already failed.
                "-ask",
                "no",
                "-nointeractive",
                "-nolookup",
                "-timeout",
                "10",
                "-Display",
                "V",
                # No custom user-agent. The obvious thing to put there is the name of
                # this project and a link to this repository -- which is the answer
                # key, announced in the request headers of every probe. nikto's own
                # default already identifies nikto, which is honest and tells a target
                # nothing about being graded.
            ]
            if share_s is not None:
                # Seconds. Soft (checked between requests) and per host, so it is set
                # well under the share: see the module docstring on losing the report.
                args += ["-maxtime", str(int(share_s * 0.8))]

            args += self._auth_args(ctx, app)
            invocations.append(
                Invocation(
                    name=app.key,
                    app=app.key,
                    args=args,
                    artifacts=[out_name],
                )
            )
        return invocations

    def _auth_args(self, ctx: RunContext, app: Any) -> list[str]:
        creds = ctx.creds_for(app.key)
        if creds is not None and creds.kind == "basic":
            # Colons inside the password break Text::ParseWords unless quoted.
            password = f'"{creds.password}"' if ":" in creds.password else creds.password
            return ["-id", f"{creds.username}:{password}"]

        session = ctx.session_for(app.key)
        if session is None:
            return []
        # nikto cannot log in at all. 2.6.x added -Add-header, which is the least
        # fragile way to replay the session the harness negotiated (the alternative,
        # -Option 'STATIC-COOKIE="..."', needs its own quoting inside the value).
        args: list[str] = []
        for name, value in session.as_headers().items():
            args += ["-Add-header", f"{name}: {value}"]
        return args

    def normalise(self, raw_dir: Path, table: CweTable | None = None, **_: Any) -> NormaliseResult:
        out = NormaliseResult()
        for path in sorted(list(raw_dir.glob("nikto-*.json")) + list(raw_dir.glob("nikto-*.xml"))):
            out.extend(normalise_nikto(path, table=table))
        return out


def _share_seconds(ctx: RunContext) -> float | None:
    if ctx.budget.wall_clock_s is None:
        return None
    return ctx.budget.wall_clock_s / max(1, len(ctx.apps))


DRIVER = NiktoDriver()
run = DRIVER.run
normalise = DRIVER.normalise
plan = DRIVER.plan
