"""nuclei driver.

nuclei is template-driven rather than crawl-driven, which makes it the most
interesting outlier in the comparison: it will find a planted CVE-shaped or
misconfiguration-shaped flaw instantly and miss an application-logic flaw entirely,
because no template describes it. The driver's job is to give it the fairest
possible shot at that: all templates, DAST/fuzzing templates on the profile that
asks for them, and the session cookie of an authenticated user.

Two decisions worth knowing about before reading a nuclei column in the results:

* **Out-of-band.** nuclei's blind payloads point at its own interactsh server on the
  public internet, not at this platform's canary, so a blind SSRF or blind XXE it
  genuinely detects still will not fire our `oracle.kind: oob` trigger. It will show
  up as a claim (scored against the catalog by URL and CWE) with no platform-side
  proof. The ``offline`` profile passes ``-ni`` to disable interactsh entirely, for
  runs that must stay hermetic; the default keeps it on because crippling a tool to
  tidy up the environment is a worse distortion than the one it fixes.
* **No global time cap.** nuclei has no flag that bounds total scan duration
  (``-timeout`` is per request). The orchestrator's budget is therefore the only
  wall-clock limit, and that is safe here: nuclei writes each result to the JSONL
  file as it is found, so a scan stopped at the deadline still leaves a valid,
  complete-up-to-that-point report. Nikto and skipfish do not have that property.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .._lib.driver import BaseDriver, Invocation, RunContext
from .._lib.findings import NormaliseResult
from .._lib.normalise import CweTable, normalise_nuclei


class NucleiDriver(BaseDriver):
    key = "nuclei"
    # The image's ENTRYPOINT is already `nuclei`, so args start at the flags.
    version_command = ["-version"]

    def plan(self, ctx: RunContext) -> list[Invocation]:
        invocations: list[Invocation] = []
        for app in ctx.apps:
            out_name = f"nuclei-{app.key}.jsonl"
            args = [
                "-target",
                app.base_url,
                # -jsonl switches the main writer to JSONL; -o is where it writes.
                # (-jsonl is a boolean: passing a path to it is a documented doc bug.)
                "-jsonl",
                "-output",
                f"{self.container_workdir}/raw/{out_name}",
                # No template auto-update: the image digest must determine what ran.
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
            # No path-exclusion flag is passed: nuclei has none, and it does not
            # crawl, so it only visits what its templates construct from the target
            # URL. The control plane is out of reach by construction, not by option.
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
