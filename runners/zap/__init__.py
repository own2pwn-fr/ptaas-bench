"""ZAP driver: Automation Framework plans, baseline and full profiles.

Why the Automation Framework rather than ``zap-baseline.py`` / ``zap-full-scan.py``:
the packaged scans hard-code a sequence and a set of tunings (passive alerts capped
at 10 instances per rule, tags disabled), which is fine for CI and wrong for a
benchmark -- the caps silently truncate exactly the evidence being measured. A plan
is also *the* artefact a reader needs to reproduce the run, and it is kept in the
run directory verbatim.

Reproducibility note: the plans deliberately do **not** run ``-addonupdate``. The ZAP
docs recommend it, but updating add-ons at run time means the recorded image digest
no longer determines what the tool did, and the whole point of the record is that it
does. Upgrading ZAP is a deliberate act: bump the tag/digest in tools.yaml.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .._lib.config import AppSpec, Credentials
from .._lib.driver import BaseDriver, Invocation, RunContext, write_text
from .._lib.findings import NormaliseResult
from .._lib.normalise import CweTable, normalise_zap

# One user entry per context; the name is referenced by the spider/ascan jobs.
USER_NAME = "bench-user"

# The target control plane must never be crawled or attacked: it can reset the app
# mid-scan. It is meant to be bound to bench-internal only; excluding it here is the
# second lock on that door.
CONTROL_PATH_RE = r".*/__bench__/.*"


class ZapDriver(BaseDriver):
    key = "zap"
    container_workdir = "/bench"
    version_command = ["zap.sh", "-cmd", "-version"]

    # -- planning ---------------------------------------------------------------

    def plan(self, ctx: RunContext) -> list[Invocation]:
        invocations: list[Invocation] = []
        share_s = _share_seconds(ctx)
        for app in ctx.apps:
            creds = ctx.creds_for(app.key)
            report_name = f"zap-{app.key}.json"
            plan = self.build_plan(ctx, app, creds, share_s=share_s, report_file=report_name)
            plan_path = write_text(
                ctx.conf_dir / f"zap-{app.key}.yaml",
                yaml.safe_dump(plan, sort_keys=False, default_flow_style=False, width=120),
            )
            env: dict[str, str] = {}
            if creds is not None:
                # Credentials travel as environment variables and are substituted by
                # ZAP's own ${} expansion, so the plan file kept as evidence in the
                # run directory contains no password.
                env["ZAP_USER"] = creds.username
                env["ZAP_PASS"] = creds.password
            invocations.append(
                Invocation(
                    name=app.key,
                    app=app.key,
                    args=[
                        "zap.sh",
                        "-cmd",
                        # No add-on update, no telemetry, no call home: see module docstring.
                        "-silent",
                        "-autorun",
                        self.in_container(ctx, plan_path),
                    ],
                    env=env,
                    artifacts=[report_name],
                    notes=f"profile={ctx.profile}",
                )
            )
        return invocations

    def build_plan(
        self,
        ctx: RunContext,
        app: AppSpec,
        creds: Credentials | None,
        *,
        share_s: float | None,
        report_file: str,
    ) -> dict[str, Any]:
        """Build the Automation Framework plan for one application."""
        full = ctx.profile != "baseline"
        minutes = _minutes(share_s)

        context: dict[str, Any] = {
            "name": app.key,
            "urls": [app.base_url.rstrip("/")],
            "includePaths": app.include_paths or [f"{app.base_url.rstrip('/')}.*"],
            "excludePaths": self._exclude_paths(app, creds),
        }
        if creds is not None:
            context.update(self._auth_blocks(app, creds))

        jobs: list[dict[str, Any]] = [
            {
                "type": "passiveScan-config",
                "parameters": {
                    "scanOnlyInScope": True,
                    # 0 = unlimited. The packaged scans cap this at 10, which would
                    # hide most instances of a stored finding -- and instances are
                    # what the scorer counts.
                    "maxAlertsPerRule": 0,
                },
            }
        ]

        # A machine-readable API description is part of the corpus: some targets
        # publish one, and whether a tool ingests it is a measured capability, not a
        # favour. Importing it when offered is the documented, intended usage.
        if app.openapi_url:
            params = {
                "apiUrl": app.openapi_url,
                "targetUrl": app.base_url.rstrip("/"),
                "context": app.key,
            }
            if creds is not None:
                params["user"] = USER_NAME
            jobs.append({"type": "openapi", "parameters": params})
        if app.graphql_url:
            # The graphql job takes neither `context` nor `user`; it inherits the
            # session from the context ZAP is already in. Do not add them.
            jobs.append(
                {
                    "type": "graphql",
                    "parameters": {
                        "endpoint": app.graphql_url,
                        "queryGenEnabled": True,
                        "requestMethod": "post_json",
                    },
                }
            )

        spider_params: dict[str, Any] = {
            "context": app.key,
            "maxDuration": minutes["spider"],
            "maxDepth": 10,
            "parseRobotsTxt": True,
            "parseSitemapXml": True,
        }
        ajax_params: dict[str, Any] = {
            "context": app.key,
            "maxDuration": minutes["ajax"],
            "maxCrawlDepth": 10,
            "browserId": "firefox-headless",
            # The corpus is deliberately SPA-heavy; running the Ajax spider only "if
            # modern" would skip the very targets the headline result is about.
            "runOnlyIfModern": False,
        }
        if creds is not None:
            spider_params["user"] = USER_NAME
            ajax_params["user"] = USER_NAME
            # Without this the spider clicks Logout and the rest of the scan is
            # anonymous while still being reported as authenticated.
            spider_params["logoutAvoidance"] = True
            ajax_params["logoutAvoidance"] = True

        jobs += [
            {"type": "spider", "parameters": spider_params},
            {"type": "spiderAjax", "parameters": ajax_params},
            {"type": "passiveScan-wait", "parameters": {"maxDuration": minutes["passive"]}},
        ]

        if full:
            ascan_params: dict[str, Any] = {
                "context": app.key,
                "maxScanDurationInMins": minutes["active"],
                "maxRuleDurationInMins": max(1, minutes["active"] // 4),
                "threadPerHost": 4,
                "delayInMs": 0,
                "handleAntiCSRFTokens": True,
                # Marks every attack request with X-ZAP-Scan-ID. Costs nothing and
                # makes it possible to attribute a trigger to a specific rule when
                # someone disputes a result.
                "injectPluginIdInHeader": True,
            }
            if creds is not None:
                ascan_params["user"] = USER_NAME
            jobs.append(
                {
                    "type": "activeScan",
                    "parameters": ascan_params,
                    "policyDefinition": {
                        "defaultStrength": "Medium",
                        "defaultThreshold": "Medium",
                    },
                }
            )
            jobs.append({"type": "passiveScan-wait", "parameters": {"maxDuration": minutes["passive"]}})

        jobs.append(
            {
                "type": "report",
                "parameters": {
                    "template": "traditional-json",
                    "reportDir": f"{self.container_workdir}/raw",
                    "reportFile": report_file,
                    "reportTitle": f"ptaas-bench {ctx.tool.key}/{ctx.profile} {app.key}",
                },
                # Everything, including informational: the scoring engine decides what
                # counts, and a driver that pre-filters is a driver that hides false
                # positives.
                "risks": ["high", "medium", "low", "info"],
                "confidences": ["high", "medium", "low", "falsepositive"],
            }
        )

        return {
            "env": {
                "contexts": [context],
                "parameters": {
                    # A failed job must not abort the plan: losing the report job
                    # because the Ajax spider tripped would lose the whole run.
                    "failOnError": False,
                    "failOnWarning": False,
                    "continueOnFailure": True,
                    "progressToStdout": True,
                },
            },
            "jobs": jobs,
        }

    def _exclude_paths(self, app: AppSpec, creds: Credentials | None) -> list[str]:
        excludes = list(app.exclude_paths) + [CONTROL_PATH_RE]
        for path in (creds.logout_paths if creds else []):
            excludes.append(f".*{path}.*")
        return excludes

    def _auth_blocks(self, app: AppSpec, creds: Credentials) -> dict[str, Any]:
        """Translate the neutral credentials file into ZAP's context dialect."""
        auth: dict[str, Any] = {}
        if creds.kind == "basic":
            host, _, port = app.host.partition(":")
            auth = {
                "method": "http",
                "parameters": {"hostname": host, "port": int(port or 80), "realm": ""},
            }
        elif creds.kind in ("json", "bearer"):
            import json as _json

            body = {**creds.login_body()}
            body[creds.username_field] = "{%username%}"
            body[creds.password_field] = "{%password%}"
            auth = {
                "method": "json",
                "parameters": {
                    "loginPageUrl": creds.login_page_url or creds.login_url,
                    "loginRequestUrl": creds.login_url,
                    "loginRequestBody": _json.dumps(body),
                },
            }
        else:
            from urllib.parse import urlencode

            body = {**creds.login_body()}
            body[creds.username_field] = "{%username%}"
            body[creds.password_field] = "{%password%}"
            # ZAP's placeholders must survive url-encoding intact.
            encoded = urlencode(body).replace("%7B%25", "{%").replace("%25%7D", "%}")
            auth = {
                "method": "form",
                "parameters": {
                    "loginPageUrl": creds.login_page_url or creds.login_url,
                    "loginRequestUrl": creds.login_url,
                    "loginRequestBody": encoded,
                },
            }

        verification: dict[str, Any] = {"method": "response"}
        if creds.logged_in_regex:
            verification["loggedInRegex"] = creds.logged_in_regex
        if creds.logged_out_regex:
            verification["loggedOutRegex"] = creds.logged_out_regex
        if creds.verify_url:
            # Polling is more robust than pattern-matching every response: it detects
            # the session dying instead of quietly scanning as an anonymous user.
            verification.update(
                {
                    "method": "poll",
                    "pollUrl": creds.verify_url,
                    "pollFrequency": 60,
                    "pollUnits": "requests",
                }
            )
        auth["verification"] = verification

        if creds.session in ("bearer", "header") and creds.token_json_path:
            header_value = creds.header_template.format(token=f"{{%json:{creds.token_json_path}%}}")
            session_management = {
                "method": "headers",
                "parameters": {creds.header_name: header_value},
            }
        else:
            session_management = {"method": "cookie"}

        return {
            "authentication": auth,
            "sessionManagement": session_management,
            "users": [
                {
                    "name": USER_NAME,
                    # ${} resolves from the container environment: no secret in the
                    # plan file that ships with the results.
                    "credentials": {"username": "${ZAP_USER}", "password": "${ZAP_PASS}"},
                }
            ],
        }

    # -- normalising ------------------------------------------------------------

    def normalise(self, raw_dir: Path, table: CweTable | None = None, **_: Any) -> NormaliseResult:
        out = NormaliseResult()
        for path in sorted(raw_dir.glob("zap-*.json")):
            out.extend(normalise_zap(path, table=table))
        return out


def _share_seconds(ctx: RunContext) -> float | None:
    if ctx.budget.wall_clock_s is None:
        return None
    return ctx.budget.wall_clock_s / max(1, len(ctx.apps))


def _minutes(share_s: float | None) -> dict[str, int]:
    """Split one application's time share across the jobs.

    These are ZAP's own soft limits; the hard limit is the orchestrator killing the
    container. Setting both means ZAP normally finishes and writes its report, rather
    than being stopped mid-scan with nothing on disk.
    """
    if share_s is None:
        return {"spider": 0, "ajax": 0, "passive": 0, "active": 0}  # 0 = unlimited in ZAP
    total = max(4.0, share_s / 60.0)
    # Reserve a tenth for the report and shutdown, so the budget kill is a backstop
    # rather than the normal path.
    usable = total * 0.9
    return {
        "spider": max(1, int(usable * 0.15)),
        "ajax": max(1, int(usable * 0.30)),
        "passive": max(1, int(usable * 0.05)),
        "active": max(1, int(usable * 0.50)),
    }


DRIVER = ZapDriver()
run = DRIVER.run
normalise = DRIVER.normalise
plan = DRIVER.plan
