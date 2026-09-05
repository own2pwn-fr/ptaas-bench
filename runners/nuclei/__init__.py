"""nuclei driver.

nuclei is template-driven rather than crawl-driven, which makes it the most
interesting outlier in the comparison: it will find a planted CVE-shaped or
misconfiguration-shaped flaw instantly and miss an application-logic flaw entirely,
because no template describes it. The driver's job is to give it the fairest possible
shot at that: a freshly updated template set, the DAST/fuzzing templates on the
profile that asks for them, and the session of an authenticated user.

Three things a reader of the nuclei column needs to know.

**Templates are fetched before the run, not during it.** The tool network is sealed
(`internal: true`, sinkhole as resolver), so nuclei cannot reach the internet while
scanning -- and it would silently carry on with whatever template set its image
happened to ship with. That would be a stale, unknown corpus of checks published as a
current one. So the update runs as a preparation step on a network with egress,
into a named volume, and the resulting template version goes into the run record. If
the update fails, the driver falls back to the image's bundled templates rather than
pointing nuclei at an empty directory -- which would have it find nothing at all --
and the record says which happened.

**Out-of-band findings will be credited to nuclei, but nuclei will not report them.**
Its blind payloads point at its own interactsh server, which the sealed network
cannot reach. The sinkhole is the resolver and the only reachable destination for
that network, so the callback is captured anyway and the platform's OOB oracle fires:
nuclei gets credit for *exercising* and *triggering* the flaw. What it cannot do is
poll its collaborator to confirm, so the finding will not appear in its own JSONL.
The `offline` profile disables interactsh entirely, which removes those payloads
altogether -- that is a strictly worse measurement and exists only for debugging.

**No global time cap.** nuclei has no flag bounding total scan duration (`-timeout`
is per request). The orchestrator's budget is the only wall-clock limit, and that is
safe here: nuclei writes each result to the JSONL file as it is found, so a scan
stopped at the deadline still leaves a valid, complete-up-to-that-point report.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .._lib.driver import BaseDriver, Invocation, PreparationResult, RunContext
from .._lib.findings import NormaliseResult
from .._lib.normalise import CweTable, normalise_nuclei

# Named volume shared between the preparation step and the scan. The name is the
# tool's own and nothing else: a named volume's source path is visible inside the
# container through /proc/self/mountinfo, so it must read as an ordinary deployment
# detail rather than as the scaffolding of a grader.
TEMPLATE_VOLUME = "nuclei-templates"
TEMPLATE_DIR = "/templates"


class NucleiDriver(BaseDriver):
    key = "nuclei"
    # The image's ENTRYPOINT is already `nuclei`, so args start at the flags.
    version_command = ["-version"]

    # -- preparation -------------------------------------------------------------

    def prepare(self, ctx: RunContext) -> list[Invocation]:
        if ctx.options.get("skip_prepare"):
            return []
        return [
            Invocation(
                name="templates",
                args=["-update-templates", "-update-template-dir", TEMPLATE_DIR],
                volumes=[(TEMPLATE_VOLUME, TEMPLATE_DIR)],
                notes="runs on a network with egress, before the run opens",
            )
        ]

    def preparation_version(
        self, ctx: RunContext, results: list[PreparationResult]
    ) -> str | None:
        """Ask nuclei which template version now sits in the volume.

        Read back from the volume rather than parsed out of the update log: the log
        wording changes between releases, and what matters is what the scan will
        actually load.
        """
        if not results or not results[0].ok:
            return None
        try:
            res = ctx.docker.run_capture(
                ctx.tool.image_ref,
                ["-templates-version", "-update-template-dir", TEMPLATE_DIR],
                network="none",
                volumes=[(TEMPLATE_VOLUME, TEMPLATE_DIR)],
                allow_pull=ctx.allow_pull,
                build_spec=ctx.tool.build,
                context_root=ctx.repo_root,
            )
        except Exception:  # noqa: BLE001 - a missing version is recorded, not fatal
            return None
        text = re.sub(r"\x1b\[[0-9;]*m", "", (res.stdout or "") + (res.stderr or ""))
        match = re.search(r"v?\d+\.\d+\.\d+", text)
        return match.group(0) if match else (text.strip().splitlines() or [None])[-1]

    # -- the scan ----------------------------------------------------------------

    def plan(self, ctx: RunContext) -> list[Invocation]:
        # False only when the preparation step ran and failed. Pointing nuclei at an
        # empty template directory makes it find nothing, which is indistinguishable
        # in the results from a tool that found nothing.
        prepared = ctx.options.get("preparation_ok", True) and not ctx.options.get("skip_prepare")

        invocations: list[Invocation] = []
        for app in ctx.apps:
            out_name = f"nuclei-{app.key}.jsonl"
            args = [
                "-target",
                app.base_url,
                # -jsonl switches the main writer to JSONL; -output is where it writes.
                # (-jsonl is a boolean: passing a path to it is a documented doc bug.)
                "-jsonl",
                "-output",
                f"{self.container_workdir}/raw/{out_name}",
                # No self-update during the run: the image digest plus the recorded
                # template version must fully determine what ran.
                "-disable-update-check",
                # Keep the raw request/response in the record. It is the evidence a
                # disputed finding is judged on, and it costs only disk.
                "-include-rr",
                "-matcher-status",
                "-stats",
                "-stats-interval",
                "60",
                "-timeout",
                "10",
                "-retries",
                "1",
                "-rate-limit",
                str(ctx.options.get("rate_limit", 50)),
                "-concurrency",
                "25",
                "-max-host-error",
                "30",
            ]
            volumes: list[tuple[str, str]] = []
            if prepared:
                args += ["-templates", TEMPLATE_DIR]
                # Read-only: the scan must not be able to modify the corpus of checks
                # that the run record claims it used.
                volumes.append((TEMPLATE_VOLUME, f"{TEMPLATE_DIR}:ro"))

            if ctx.profile == "dast":
                # Fuzzing templates: the only nuclei mode that attacks parameters
                # rather than fingerprinting known software.
                args += ["-dast"]
            if ctx.profile == "offline" or ctx.options.get("offline"):
                args += ["-no-interactsh"]

            session = ctx.session_for(app.key)
            if session is not None:
                # nuclei cannot log in. `-header` is the whole of its auth support,
                # so the harness logs in first and hands over the result.
                for name, value in session.as_headers().items():
                    args += ["-header", f"{name}: {value}"]

            invocations.append(
                Invocation(
                    name=app.key,
                    app=app.key,
                    args=args,
                    volumes=volumes,
                    artifacts=[out_name],
                    notes="authenticated via injected header" if session else "anonymous",
                )
            )
        return invocations

    def normalise(self, raw_dir: Path, table: CweTable | None = None, **_: Any) -> NormaliseResult:
        out = NormaliseResult()
        for path in sorted(raw_dir.glob("nuclei-*.jsonl")):
            out.extend(normalise_nuclei(path, table=table))
        return out


DRIVER = NucleiDriver()
run = DRIVER.run
normalise = DRIVER.normalise
plan = DRIVER.plan
prepare = DRIVER.prepare
