"""Checks that must pass before a run opens.

Every check here exists because its failure mode is a plausible-looking result rather
than an error. A run against a target that is not there does not crash: it produces a
findings file with nothing in it, and that reads in the comparison table as a scanner
with poor coverage. The whole point of stopping early is that "the harness was
misconfigured" and "the tool missed everything" must never be the same output.

Five checks:

* **The target's credentials file exists.** It is the authority for the base URL
  (seed-derived, so it cannot be hardcoded) and for the identities. Missing, we do not
  know where the application is.
* **The tool-facing hostname resolves to the target, from the tool's own network.**
  The sinkhole is the resolver for that network and answers everything, so a name that
  is wrong does not fail to resolve -- it resolves to the sinkhole, and the tool spends
  its whole budget scanning the sinkhole.
* **A target that declares users has them.** The contract asks a target with no login
  to write the file with an empty ``users:`` list and a comment, precisely so that "no
  authentication here" can be told from "someone forgot". Only the second stops a run,
  and only when the catalog says that application has authenticated entrypoints.
* **The identities are the ones the running deployment actually has.** The committed
  credentials file belongs to one DEPLOY_SEED; e-mail addresses and passwords move
  with the seed while subject ids do not. So the target is asked, with
  ``state-reset --emit-credentials``, and where it disagrees with the file the target
  wins. Trusting a file committed for the default seed is how an authenticated run
  quietly becomes an anonymous one.
* **No `dev`-profile service is running.** Those sit on a non-internal network to give
  a developer host access. Up during a run, that is a route out of the sealed network,
  and the egress capture that makes every blind vulnerability in the corpus measurable
  stops working -- silently, because the callbacks simply leave instead.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .config import REPO_ROOT, AppSpec, BenchConfig

log = logging.getLogger("bench.runners.preflight")

# Compose profiles whose services must never be up during a measured run.
FORBIDDEN_PROFILES = ("dev",)


class PreflightError(RuntimeError):
    """A condition that makes the run's result meaningless. Stop before opening it."""


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""
    # A check that could not be performed is neither pass nor fail: it is recorded
    # and does not stop the run, because refusing to run over an unverifiable
    # condition is its own kind of wrong answer.
    indeterminate: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ok": self.ok,
            "indeterminate": self.indeterminate,
            "detail": self.detail,
        }


@dataclass
class PreflightReport:
    checks: list[Check] = field(default_factory=list)

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if not c.ok and not c.indeterminate]

    @property
    def ok(self) -> bool:
        return not self.failures

    def to_dict(self) -> list[dict[str, Any]]:
        return [c.to_dict() for c in self.checks]

    def raise_if_failed(self) -> None:
        if self.failures:
            detail = "; ".join(f"{c.name}: {c.detail}" for c in self.failures)
            raise PreflightError(
                "refusing to open a run -- these conditions would produce a result that "
                f"looks like a tool finding nothing: {detail}"
            )


def apps_with_authenticated_entrypoints(catalog_dir: Path | None = None) -> set[str]:
    """Applications the catalog says have flaws behind a login.

    Read from the catalog rather than assumed, so a target that genuinely has no
    authenticated surface is not required to invent credentials.
    """
    catalog_dir = catalog_dir or (REPO_ROOT / "catalog" / "vulns")
    apps: set[str] = set()
    if not catalog_dir.exists():
        return apps
    for path in sorted(catalog_dir.glob("*.yaml")):
        try:
            doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            continue
        entrypoint = doc.get("entrypoint") or {}
        if doc.get("app") and str(entrypoint.get("auth", "none")) != "none":
            apps.add(str(doc["app"]))
    return apps


def emit_credentials(docker: Any, app: AppSpec) -> tuple[dict[str, Any] | None, str]:
    """Ask the running target for its current identities.

    ``state-reset --emit-credentials`` prints the `app:` and `users:` blocks and
    changes nothing. A target that does not implement the flag is not an error: the
    committed file is then all there is, and the check says so.
    """
    res = docker.compose_exec(
        app.reset_service, [app.reset_command, "--emit-credentials"], timeout=120
    )
    if res.returncode != 0:
        return None, f"exit {res.returncode} {(res.stderr or '').strip()[:200]}".strip()
    try:
        doc = yaml.safe_load(res.stdout or "") or {}
    except yaml.YAMLError as exc:
        return None, f"unparsable output: {exc}"
    if not isinstance(doc, dict) or "users" not in doc:
        return None, f"no users block in the output: {(res.stdout or '').strip()[:200]!r}"
    return doc, "ok"


def check_live_credentials(config: BenchConfig, app: AppSpec, docker: Any) -> Check:
    """Overlay the live identities on the committed file, and say whether they differ."""
    name = f"credentials-live:{app.key}"
    committed = config.target_file(app)
    if committed is None:
        return Check(name, True, "no committed file to reconcile", indeterminate=True)

    doc, detail = emit_credentials(docker, app)
    if doc is None:
        return Check(
            name,
            False,
            f"{app.reset_command} --emit-credentials did not answer ({detail}); the "
            "committed file is being trusted, and it belongs to one DEPLOY_SEED",
            indeterminate=True,
        )

    config.emitted_credentials[app.key] = doc
    before = {(u.get("role"), u.get("username")) for u in (committed.raw.get("users") or [])}
    after = {(u.get("role"), u.get("username")) for u in (doc.get("users") or [])}
    if before != after:
        return Check(
            name,
            True,
            "the committed file belongs to a different DEPLOY_SEED; using the "
            f"identities the target printed ({sorted(r for r, _ in after)})",
        )
    return Check(name, True, "the committed file matches the running deployment")


def js_dependent_entries(apps: list[AppSpec], catalog_dir: Path | None = None) -> dict[str, int]:
    """How many catalog entries per app need JavaScript execution.

    Read from ``discovery.requires``, which is where the catalog states it. A tool
    with no browser will miss these, and that is an honest result for that tool --
    but only if the reader knows, which is why it goes into the run record next to
    whether the tool had a browser at all.
    """
    catalog_dir = catalog_dir or (REPO_ROOT / "catalog" / "vulns")
    counts = {app.key: 0 for app in apps}
    if not catalog_dir.exists():
        return counts
    for path in sorted(catalog_dir.glob("*.yaml")):
        try:
            doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            continue
        app = str(doc.get("app", ""))
        requires = ((doc.get("discovery") or {}).get("requires")) or []
        if app in counts and "js-execution" in requires:
            counts[app] += 1
    return counts


def check_credentials(config: BenchConfig, app: AppSpec, authed_apps: set[str]) -> list[Check]:
    checks: list[Check] = []
    target = config.target_file(app)
    path = app.default_credentials_file()

    if target is None:
        override = config.credentials_override.get(app.key)
        if override is not None:
            checks.append(
                Check(
                    f"credentials:{app.key}",
                    True,
                    f"{path} is absent; using the local override in runners/credentials.yaml. "
                    "That file is not seed-derived and will be wrong after a redeploy.",
                )
            )
        else:
            checks.append(
                Check(
                    f"credentials:{app.key}",
                    False,
                    f"{path} does not exist. The target writes it at seed time and it is "
                    "the authority for the base URL and the identities; without it we do "
                    "not know where the application is.",
                )
            )
        return checks

    checks.append(Check(f"credentials:{app.key}", True, f"{path}"))

    if target.declares_no_login:
        needs_auth = app.key in authed_apps
        checks.append(
            Check(
                f"credentials-users:{app.key}",
                not needs_auth,
                (
                    "the file declares no users, but the catalog has authenticated "
                    "entrypoints for this application: every one of them would be "
                    "reported as missed by every tool"
                )
                if needs_auth
                else "the file declares no users, and the catalog has no authenticated "
                "entrypoints here -- this target has no login, which is a legitimate answer",
            )
        )
    else:
        checks.append(
            Check(f"credentials-users:{app.key}", True, f"roles: {', '.join(target.roles)}")
        )

    if not target.base_url and not app.base_url:
        checks.append(
            Check(
                f"base-url:{app.key}",
                False,
                f"{path} declares no base_url and apps.yaml has no fallback",
            )
        )
    return checks


def check_dns_from_tool_network(
    docker: Any, app: AppSpec, image: str, expected: list[str], *, network: str, allow_pull: bool
) -> Check:
    """Resolve the tool-facing hostname the way the tool will, and check the answer.

    Performed from a container on the tool's own network, using the image already
    pulled for this run rather than anything new. The sinkhole answers every name on
    that network, so the interesting failure is not "does not resolve" but "resolves
    to the sinkhole instead of to the target" -- which spends a whole budget scanning
    the sinkhole and reports it as a tool that found nothing.
    """
    host = app.host.split(":")[0]
    name = f"dns:{app.key}"
    if not host:
        return Check(name, False, "no tool-facing host to resolve")
    # `getent hosts` is in busybox, coreutils and glibc images alike; falling back to
    # `nslookup` covers the rest. If neither exists the check is indeterminate, not
    # failed: an unverifiable condition must not block a run on its own.
    script = f"getent hosts {host} 2>/dev/null || nslookup {host} 2>/dev/null || exit 42"
    try:
        res = docker.run_capture(
            image, ["-c", script], entrypoint="/bin/sh", network=network, allow_pull=allow_pull
        )
    except Exception as exc:  # noqa: BLE001
        return Check(name, False, f"could not resolve {host}: {exc}", indeterminate=True)

    output = (res.stdout or "") + (res.stderr or "")
    if res.returncode == 42:
        return Check(
            name, False, f"no resolver tool in {image}; {host} not verified", indeterminate=True
        )
    if res.returncode != 0 or not output.strip():
        return Check(name, False, f"{host} does not resolve from {network}: {output.strip()[:200]}")

    resolved = [token for token in output.replace("\n", " ").split() if _looks_like_ip(token)]
    if expected and resolved and not any(ip in expected for ip in resolved):
        return Check(
            name,
            False,
            f"{host} resolves to {resolved} from {network}, but this target answers on "
            f"{expected}. A name that resolves to the sinkhole instead of the target "
            "spends the whole budget scanning the sinkhole.",
        )
    return Check(name, True, f"{host} -> {resolved or output.strip()[:80]}")


def _looks_like_ip(token: str) -> bool:
    parts = token.split(".")
    return len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts)


def check_no_dev_services(docker: Any, profiles: tuple[str, ...] = FORBIDDEN_PROFILES) -> Check:
    """Refuse to run while a `dev`-profile service is up.

    Those services exist to publish a target on a developer's machine, which is
    exactly the route the sealed network removes. Up during a run, a tool has a way
    out and a compromised target has a way to the host -- and the egress capture the
    blind-vulnerability oracles depend on stops seeing the callbacks.
    """
    try:
        services = docker.compose_services_by_profile()
    except Exception as exc:  # noqa: BLE001
        return Check("dev-profile", False, f"could not read the compose config: {exc}", indeterminate=True)
    if services is None:
        return Check("dev-profile", False, "compose config unavailable", indeterminate=True)

    flagged = [name for name, profs in services.items() if set(profs) & set(profiles)]
    running = [name for name in flagged if docker.compose_ps_id(name)]
    if running:
        return Check(
            "dev-profile",
            False,
            f"{running} are up. They sit on a non-internal network, so the tool has a "
            "route out and out-of-band callbacks leave instead of being captured -- "
            "every blind vulnerability would read as missed by every tool.",
        )
    return Check("dev-profile", True, f"none of {flagged or 'no dev services'} are running")


def preflight(
    config: BenchConfig,
    apps: list[AppSpec],
    docker: Any,
    *,
    tool_image: str | None = None,
    topology: dict[str, Any] | None = None,
    allow_pull: bool = True,
    check_dns: bool = True,
) -> PreflightReport:
    report = PreflightReport()
    authed_apps = apps_with_authenticated_entrypoints()

    report.checks.append(check_no_dev_services(docker))

    for app in apps:
        # Ask the target first: the answer changes what check_credentials reads.
        if config.target_file(app) is not None and app.reset_service:
            report.checks.append(check_live_credentials(config, app, docker))
        report.checks.extend(check_credentials(config, app, authed_apps))
        if check_dns and tool_image:
            expected = list((topology or {}).get(app.key, []))
            report.checks.append(
                check_dns_from_tool_network(
                    docker, app, tool_image, expected, network=config.network, allow_pull=allow_pull
                )
            )
    for check in report.checks:
        if check.indeterminate:
            log.warning("preflight %s could not be verified: %s", check.name, check.detail)
        elif not check.ok:
            log.error("preflight %s failed: %s", check.name, check.detail)
    return report
