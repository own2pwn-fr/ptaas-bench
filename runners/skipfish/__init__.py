"""skipfish driver -- the fifth scanner, chosen over Arachni.

Why skipfish and not Arachni, since the brief allowed either:

1. **Licence, as we read it.** Arachni ships under the Arachni Public Source License
   v1.0, which is not OSI-approved and restricts use involving "Commercialization",
   a term the licence defines to include value-added services and SaaS, with
   exemptions for a pen-tester's own toolkit and for assessing your own systems. We
   are not confident that a public competitive benchmark published by the vendor of a
   competing product falls inside those exemptions, and redistributing an image or
   fixtures raises the same question. We did not seek permission; we chose a tool
   that does not require the analysis. This is our reading, not a legal opinion and
   not a claim about the project or its authors. skipfish is Apache-2.0: nothing to
   interpret, fixtures redistributable, no footnote under the results table.
2. **Reproducible acquisition.** Arachni's repository is archived, it has no release
   assets, no upstream image and no distribution package; the only image carrying a
   current version is a single unsigned community build, amd64 only, with no
   published Dockerfile. skipfish is still packaged in Kali (2.10b-2kali8, accepted
   2025-12), so the image below is built from a maintained, multi-arch base with an
   auditable packaging trail.
3. **We would have to disable its browser to run it.** 1.6.x hard-exits on a DOM scan
   unless chromedriver matches its 2022 selenium pin, and the practical workaround is
   ``--browser-cluster-pool-size=0``, which disables all JavaScript coverage. On a
   deliberately SPA-heavy corpus, publishing numbers for a scanner with its browser
   turned off would be worse than not publishing them.

The cost of the choice, stated plainly because it shows up in the results: Arachni's
report carries an integer CWE per issue and skipfish carries none, so every skipfish
finding depends on the ``skipfish`` section of ``_lib/cwe_map.yaml`` keyed by its
numeric issue type, and any type not in that table is emitted with ``cwe: null``.
skipfish also reports no HTTP method, and its ``samples.js`` roll-up is capped at
1024 samples per issue type. skipfish is also, unambiguously, dead upstream (last
commit 2012): it is in the comparison as a historical baseline, not as a live
contender, and the results should say so.

Operational invariants encoded below, each of which otherwise breaks the run:
* ``-o`` must point at a directory that does **not** exist -- skipfish calls
  ``rmdir()`` then ``mkdir()`` on it, so aiming it at the bind mount is fatal.
* ``-W`` rewrites the wordlist in place at the end of a scan, so the dictionary is
  copied per run; otherwise run N+1 starts from run N's mutated dictionary and the
  runs stop being comparable.
* ``-k`` is parsed as ``h:m:s`` with left-to-right multipliers, so a bare ``-k 600``
  means 600 *hours*. All three fields are always passed.
"""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

from .._lib.driver import BaseDriver, Invocation, RunContext
from .._lib.findings import NormaliseResult
from .._lib.normalise import CweTable, normalise_skipfish

DEFAULT_WORDLIST = "/usr/share/skipfish/dictionaries/complete.wl"
FALLBACK_WORDLIST = "/usr/share/skipfish/dictionaries/minimal.wl"

# Fixed, so two runs of the same version against the same target explore in the same
# order. skipfish's crawl and its injected markers are derived from this seed, so it
# is an arbitrary constant rather than anything spelling out a word: the markers end
# up in the target's logs.
SCAN_SEED = "0x5c8f2a11"


class SkipfishDriver(BaseDriver):
    key = "skipfish"
    # skipfish needs a shell: the wordlist has to be copied before the scan starts.
    default_entrypoint = "/bin/sh"
    # skipfish has no --version flag at all. The Dockerfile records the Debian
    # package version at build time, which is a better answer anyway: "2.10b" has
    # been the upstream version since 2012, while the Kali package is what actually
    # changed. The probe must go through the shell, like the scan.
    version_entrypoint = "/bin/sh"
    version_command = ["-c", "cat /skipfish.version 2>/dev/null || echo 'skipfish (version unknown)'"]

    def plan(self, ctx: RunContext) -> list[Invocation]:
        share_s = _share_seconds(ctx)
        invocations: list[Invocation] = []
        for app in ctx.apps:
            out_dir = f"{self.container_workdir}/raw/skipfish-{app.key}"
            wordlist = ctx.options.get("wordlist", DEFAULT_WORDLIST)

            scan = [
                "skipfish",
                "-o",
                out_dir,
                "-W",
                "/tmp/wordlist.wl",
                # Quiet: skipfish's live UI expects a TTY and floods the log file.
                "-u",
                "-q",
                SCAN_SEED,
                "-d",
                "10",
                "-c",
                "32",
                "-x",
                "512",
                "-l",
                str(ctx.options.get("rate_limit", 30)),
                "-g",
                "20",
                "-m",
                "5",
                "-t",
                "10",
            ]
            if share_s is not None:
                scan += ["-k", _hms(share_s * 0.85)]
            if ctx.budget.max_requests:
                # skipfish is the only tool here that can enforce the request budget
                # itself, so it is told about it as well as being metered externally.
                scan += ["-r", str(ctx.budget.max_requests)]

            scan += self._exclusions(ctx, app)
            scan += self._auth_args(ctx, app)
            scan += [app.base_url]

            # Built as a shell string because of the wordlist copy. Every interpolated
            # value goes through shlex.quote: these come from config files, and a
            # benchmark harness that can be turned into a shell by a target URL is
            # not one anybody should run.
            script = (
                f"set -e; "
                f"if [ -f {shlex.quote(wordlist)} ]; then cp {shlex.quote(wordlist)} /tmp/wordlist.wl; "
                f"else cp {shlex.quote(FALLBACK_WORDLIST)} /tmp/wordlist.wl; fi; "
                f"exec {shlex.join(scan)}"
            )

            invocations.append(
                Invocation(
                    name=app.key,
                    app=app.key,
                    args=["-c", script],
                    artifacts=[f"skipfish-{app.key}/samples.js"],
                    notes="report is JS, not JSON; see _lib/normalise.normalise_skipfish",
                )
            )
        return invocations

    def _exclusions(self, ctx: RunContext, app: Any) -> list[str]:
        # -X blacklists a substring of the URL. The logout is the one thing a scanner
        # must not reach: it would spend the rest of the run as an anonymous user
        # while still being reported as authenticated.
        args: list[str] = []
        creds = ctx.creds_for(app.key)
        # -X is a substring match, so a route template would never fire.
        for path in (creds.logout_prefixes() if creds else []):
            args += ["-X", path]
        for path in list(getattr(app, "exclude_paths", []) or []):
            args += ["-X", path]
        return args

    def _auth_args(self, ctx: RunContext, app: Any) -> list[str]:
        creds = ctx.creds_for(app.key)
        args: list[str] = []
        if creds is not None and creds.kind == "basic":
            return ["-A", f"{creds.username}:{creds.password}"]
        session = ctx.session_for(app.key)
        if session is None:
            return args
        # skipfish's own --auth-form flow exists, but it re-authenticates by
        # submitting a form it guesses at; replaying the verified session the harness
        # already holds is both simpler and provably logged in.
        for name, value in session.cookies.items():
            args += ["-C", f"{name}={value}"]
        for name, value in session.headers.items():
            args += ["-H", f"{name}={value}"]
        return args

    def normalise(self, raw_dir: Path, table: CweTable | None = None, **_: Any) -> NormaliseResult:
        out = NormaliseResult()
        for path in sorted(raw_dir.glob("skipfish-*")):
            if path.is_dir():
                out.extend(normalise_skipfish(path, table=table))
        return out


def _hms(seconds: float) -> str:
    """Format for skipfish's ``-k``: always three fields (see module docstring)."""
    total = max(60, int(seconds))
    return f"{total // 3600}:{(total % 3600) // 60}:{total % 60}"


def _share_seconds(ctx: RunContext) -> float | None:
    if ctx.budget.wall_clock_s is None:
        return None
    return ctx.budget.wall_clock_s / max(1, len(ctx.apps))


DRIVER = SkipfishDriver()
run = DRIVER.run
normalise = DRIVER.normalise
plan = DRIVER.plan
