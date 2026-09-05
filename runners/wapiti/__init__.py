"""wapiti driver.

wapiti is the only tool in the set with a real global wall-clock flag
(``--max-scan-time``), so its invocation can be made to land inside the budget by
itself instead of being killed at the deadline. The orchestrator's kill stays as a
backstop.

Two things this driver always does, both learned the hard way:

* ``--flush-session --flush-attacks``. wapiti persists a sqlite session per target
  and *resumes* it on the next run, silently skipping work it thinks it already did.
  In a benchmark that means the second tool run against the same target scans less
  than the first and looks worse for it. Reproducibility requires starting cold.
* It logs in natively when it can. wapiti's ``--form-user/--form-password/--form-url``
  drives the application's own login form, which exercises the login flow the way a
  real scan would; only when that is not applicable (JSON/bearer logins) does the
  driver fall back to injecting the session the harness obtained.

Image: there is no published official wapiti image (the upstream repo builds one in
CI but never pushes it, and cyberwatch/wapiti is amd64-only and predates 3.3.x), so
the harness builds one from ``runners/wapiti/Dockerfile``, pinned to a released
version on PyPI. tools.yaml carries the build stanza.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .._lib.driver import BaseDriver, Invocation, RunContext
from .._lib.findings import NormaliseResult
from .._lib.normalise import CweTable, normalise_wapiti

# All attack modules that make sense against an HTTP target. `ssl` is excluded: it
# needs the external sslscan binary and the corpus is served over plain HTTP inside
# the bench networks, so it would only ever produce noise.
MODULES = (
    "backup,brute_login_form,buster,cms,cookieflags,crlf,csp,csrf,exec,file,htaccess,"
    "htp,http_headers,https_redirect,ldap,log4shell,methods,network_device,nikto,"
    "permanentxss,redirect,shellshock,spring4shell,sql,ssrf,takeover,timesql,upload,"
    "wapp,wp_enum,xss,xxe"
)


class WapitiDriver(BaseDriver):
    key = "wapiti"
    version_command = ["--version"]

    def plan(self, ctx: RunContext) -> list[Invocation]:
        share_s = _share_seconds(ctx)
        invocations: list[Invocation] = []
        for app in ctx.apps:
            out_name = f"wapiti-{app.key}.json"
            args = [
                "--url",
                app.base_url,
                "--format",
                "json",
                "--output",
                f"{self.container_workdir}/raw/{out_name}",
                "--module",
                ctx.options.get("modules", MODULES),
                # `folder` keeps the scan under the start URL's directory. The start
                # URL is the site root, so this is "the whole application and nothing
                # else" -- `domain` would wander onto sibling targets.
                "--scope",
                "folder",
                "--depth",
                "10",
                "--max-links-per-page",
                "100",
                "--tasks",
                "8",
                "--timeout",
                "10",
                "--verify-ssl",
                "0",
                # Start cold, every time. See the module docstring.
                "--flush-session",
                "--flush-attacks",
                # Keeps the request/response of each finding in the report, which is
                # what an audit of a disputed result needs.
                "--detailed-report",
                "1",
                "--verbose",
                "1",
            ]
            if share_s is not None:
                # Plain seconds, no suffix. 85% of the share so wapiti writes its
                # report itself rather than being stopped by the orchestrator.
                args += ["--max-scan-time", str(int(share_s * 0.85))]
                args += ["--max-attack-time", str(max(60, int(share_s * 0.25)))]

            args += self._exclusions(ctx, app)
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

    def _exclusions(self, ctx: RunContext, app: Any) -> list[str]:
        args: list[str] = []
        base = app.base_url.rstrip("/")
        for path in list(getattr(app, "exclude_paths", []) or []):
            args += ["--exclude", f"{base}{path}"]
        creds = ctx.creds_for(app.key)
        for path in (creds.logout_paths if creds else []):
            args += ["--exclude", f"{base}{path}"]
        return args

    def _auth_args(self, ctx: RunContext, app: Any) -> list[str]:
        creds = ctx.creds_for(app.key)
        if creds is None:
            return []
        if creds.kind == "basic":
            return [
                "--auth-user",
                creds.username,
                "--auth-password",
                creds.password,
                "--auth-method",
                "basic",
            ]
        if creds.kind == "form" and (creds.login_page_url or creds.login_url):
            # Native form login: wapiti finds the form and submits it itself.
            return [
                "--form-user",
                creds.username,
                "--form-password",
                creds.password,
                "--form-url",
                creds.login_page_url or creds.login_url,
            ]
        # JSON / bearer logins: wapiti has no equivalent, so replay the session the
        # harness negotiated. `-C` (capital) is a literal cookie string; `-c`
        # (lowercase) is a cookie *file* and would fail here.
        session = ctx.session_for(app.key)
        if session is None:
            return []
        args: list[str] = []
        cookie = session.cookie_header
        if cookie:
            args += ["--cookie-value", cookie]
        for name, value in session.headers.items():
            args += ["--header", f"{name}: {value}"]
        return args

    def normalise(self, raw_dir: Path, table: CweTable | None = None, **_: Any) -> NormaliseResult:
        out = NormaliseResult()
        for path in sorted(raw_dir.glob("wapiti-*.json")):
            out.extend(normalise_wapiti(path, table=table))
        return out


def _share_seconds(ctx: RunContext) -> float | None:
    if ctx.budget.wall_clock_s is None:
        return None
    return ctx.budget.wall_clock_s / max(1, len(ctx.apps))


DRIVER = WapitiDriver()
run = DRIVER.run
normalise = DRIVER.normalise
plan = DRIVER.plan
