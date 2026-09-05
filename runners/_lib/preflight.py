"""Checks that must pass before a run opens.

Every check here exists because its failure mode is a plausible-looking result rather
than an error. A run against a target that is not there does not crash: it produces a
findings file with nothing in it, and that reads in the comparison table as a scanner
with poor coverage. The whole point of stopping early is that "the harness was
misconfigured" and "the tool missed everything" must never be the same output.

Nine checks:

* **The target's credentials file exists.** It is the authority for the base URL
  (seed-derived, so it cannot be hardcoded) and for the identities. Missing, we do not
  know where the application is.
* **The tool-facing hostname resolves to the target, from the tool's own network.**
  The sinkhole is the resolver for that network and answers everything, so a name that
  is wrong does not fail to resolve -- it resolves to the sinkhole, and the tool spends
  its whole budget scanning the sinkhole.
* **The base URL actually answers on the tool's network.** Resolving is not enough:
  a name resolves through the sinkhole whatever happens, and a base URL naming the
  wrong port (no port where the application listens on 8080) resolves perfectly and
  then refuses every connection. The tool spends its whole budget collecting
  connection errors and is published as having found nothing.
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
* **No service on the tool's network publishes a host port**, whatever profile it
  claims. Publishing does not breach the egress seal, but it puts a target on a
  developer's loopback during a measured run and lets traffic reach it without
  passing the client accounting the origin performs -- and that accounting is what
  decides whether a request is scored as the tool's.
* **The name the harness connects by is platform-side.** ``base_url`` is what the
  tool uses and must be public; ``internal_url`` is what the harness uses and must
  resolve to an address in the platform's range. The two fields look interchangeable
  and are not: connect by a name that resolves on the tool's network and the
  harness's own preflight and login traffic arrives from an address the target
  classifies as a tool's, and is credited to whichever tool is running as crawl
  coverage. When the target offers a name that is platform-side only -- infra's
  ``web01``, ``cache01``, ``ops01`` -- not using it is a configuration error here,
  not a property of the target, so it is fatal.
* **Name ambiguity is reported.** A service whose name or alias exists on both
  networks makes interface selection a coin toss for anything that connects by name.
  The harness pins its own logins to the internal address, but the ambiguity is a
  property of the target, so it is recorded rather than silently worked around.
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


def check_live_credentials(
    config: BenchConfig, app: AppSpec, docker: Any, *, fatal_on_mismatch: bool = True
) -> Check:
    """Overlay the live identities on the committed file, and reconcile the two.

    A disagreement is fatal by default when the catalog says this application has
    flaws behind a login. It means the committed file belongs to a different
    DEPLOY_SEED than the instance in front of us, and everything that reads that file
    -- the target's own selftest, the scorer's view of which subject owns what -- is
    then describing a different deployment from the one being scanned. The harness
    could quietly scan with the live identities and produce a plausible run, which is
    exactly the problem: the published result would rest on credentials nobody can
    reproduce from the repository.
    """
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
        detail = (
            f"{committed.path} does not match the running deployment: it lists "
            f"{sorted(u for _, u in before)} and the target reports "
            f"{sorted(u for _, u in after)}. The file belongs to a different "
            "DEPLOY_SEED, so anything else reading it -- the target's selftest, the "
            "scorer's view of which subject owns what -- describes a different "
            "deployment from the one being scanned. Regenerate it:\n"
            f"    docker compose exec {app.reset_service} {app.reset_command} "
            f"--emit-credentials > {committed.path}"
        )
        # The live identities are used regardless, so that --allow-stale-credentials
        # produces a correct scan rather than an anonymous one.
        return Check(name, not fatal_on_mismatch, detail)
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


def _resolved_network_names(doc: dict[str, Any]) -> dict[str, str]:
    """compose network key -> the real docker network name."""
    return {
        key: str((spec or {}).get("name") or key)
        for key, spec in (doc.get("networks") or {}).items()
    }


def check_no_published_ports(docker: Any, network: str) -> Check:
    """Refuse to run while anything on the tool's network publishes a host port.

    Whatever profile it claims: a port published outside any profile is up whenever
    the target is, and six more targets are still landing.
    """
    doc = docker.compose_config()
    if doc is None:
        return Check("published-ports", False, "could not read the compose config", indeterminate=True)

    names = _resolved_network_names(doc)
    offenders: list[str] = []
    for service, spec in (doc.get("services") or {}).items():
        spec = spec or {}
        if not spec.get("ports"):
            continue
        attached = {names.get(key, key) for key in (spec.get("networks") or {})}
        if network in attached:
            published = [
                str(p.get("published") if isinstance(p, dict) else p) for p in spec["ports"]
            ]
            offenders.append(f"{service} ({', '.join(published)})")
    if offenders:
        return Check(
            "published-ports",
            False,
            f"{'; '.join(offenders)} publish host ports while attached to {network}. "
            "Publishing does not breach the egress seal, but it puts a target on a "
            "developer's loopback during a measured run and lets traffic reach it "
            "without passing the client accounting the origin performs -- which is "
            "what decides whether a request is scored as the tool's. Gate it behind a "
            "`dev` profile, as edge-devtap does.",
        )
    return Check("published-ports", True, f"nothing on {network} publishes a host port")


def check_name_ambiguity(docker: Any, app: AppSpec, networks: tuple[str, str]) -> Check:
    """Report a name that is ambiguous in the one way that can change a score.

    The condition worth reporting is NOT "this name resolves on two networks":
    Docker's embedded resolver guarantees that for the bare service name of every
    attached service, so reporting it would fire on every dual-homed target and mean
    nothing. It is:

        a name the harness or a tool actually connects by is ambiguous between two
        networks whose source addresses are classified differently.

    Only that can change a number. The two networks here are the tool's and the
    platform's, and which one a connection leaves by decides the source address,
    which decides whether the target records the request as the platform's own
    traffic or as the tool's crawl coverage.

    Informational, not fatal: it is how the target chose to name itself, and the
    harness already pins its own connections to the internal address rather than
    trusting the resolver. But anything else that reaches the target by name -- a
    probe, a future component, a person with curl -- gets whichever interface the
    resolver felt like.
    """
    name = f"name-ambiguity:{app.key}"
    doc = docker.compose_config()
    if doc is None:
        return Check(name, True, "could not read the compose config", indeterminate=True)

    resolved = _resolved_network_names(doc)
    seen: dict[str, set[str]] = {}
    for service in app.services:
        spec = (doc.get("services") or {}).get(service) or {}
        for key, attachment in (spec.get("networks") or {}).items():
            network = resolved.get(key, key)
            for alias in [service, *((attachment or {}).get("aliases") or [])]:
                seen.setdefault(str(alias), set()).add(network)

    # Narrowed to the names that are actually connected by. The bare service name is
    # deliberately still collected above, because one of these two may *be* it.
    in_use = {n for n in (app.host.split(":")[0], app.internal_host) if n}
    both = sorted(n for n in in_use if set(networks) <= seen.get(n, set()))
    if both:
        return Check(
            name,
            True,
            f"{both} resolve on both {networks[0]} and {networks[1]}; the harness pins "
            "its own connections to the internal address, but anything connecting by "
            "name gets whichever interface the resolver returns, and the interface "
            "decides whether the traffic is scored as the tool's.",
        )
    return Check(name, True, "the names in use resolve on one network each")


def check_internal_name_is_platform_side(
    docker: Any, app: AppSpec, networks: tuple[str, str]
) -> Check:
    """The name the harness connects by must not resolve on the tool's network.

    The bare compose service name resolves on every network the service is attached
    to, so `infra-web` reaches the target over the tool's network and our traffic
    arrives from an address the target classifies as a scanner's. The operations
    aliases -- `web01`, `cache01`, `ops01` -- exist precisely so that platform
    traffic can be told apart, and the target's own self-test already defaults to
    them.

    Fatal when the target offers a platform-side-only name and this file does not use
    it: that is a mistake in apps.yaml, and it is ours to fix. Not fatal when no such
    name exists (shopfront and intranet are reachable under one name on both
    networks by design) -- there the harness pins the connection to the internal
    address instead, and check_name_ambiguity reports it.
    """
    public_net, internal_net = networks
    name = f"internal-name:{app.key}"
    doc = docker.compose_config()
    if doc is None:
        return Check(name, True, "could not read the compose config", indeterminate=True)

    resolved = _resolved_network_names(doc)
    # service -> {alias -> set(networks)}
    per_service: dict[str, dict[str, set[str]]] = {}
    for service in app.services:
        spec = (doc.get("services") or {}).get(service) or {}
        names: dict[str, set[str]] = {}
        for key, attachment in (spec.get("networks") or {}).items():
            network = resolved.get(key, key)
            for alias in [service, *((attachment or {}).get("aliases") or [])]:
                names.setdefault(str(alias), set()).add(network)
        per_service[service] = names

    host = app.internal_host
    owner = next((svc for svc, names in per_service.items() if host in names), None)
    if owner is None:
        return Check(
            name, True, f"{host!r} is not an alias of any service of this app", indeterminate=True
        )

    nets = per_service[owner].get(host, set())
    if public_net not in nets:
        return Check(name, True, f"{host!r} resolves on {internal_net} only")

    alternatives = sorted(
        alias
        for alias, alias_nets in per_service[owner].items()
        if internal_net in alias_nets and public_net not in alias_nets
    )
    if alternatives:
        return Check(
            name,
            False,
            f"internal_url uses {host!r}, which also resolves on {public_net}: the "
            "harness's own preflight and login traffic would arrive from an address "
            "the target classifies as a tool's and be credited to whichever tool is "
            f"running. {owner} offers {alternatives}, which resolve on {internal_net} "
            "only -- use one of those.",
        )
    return Check(
        name,
        True,
        f"{host!r} resolves on both networks and {owner} offers no platform-side-only "
        "alias; the harness pins its connections to the internal address instead.",
    )


def check_base_url_reachable(http: Any, app: AppSpec, address: str | None) -> Check:
    """Open one connection to the tool-facing base URL, from the tool's network.

    DNS resolution is not reachability. The sinkhole answers every name, so a base
    URL that names the wrong port -- ``http://hub.example`` where the application
    listens on 8080 -- resolves perfectly and refuses every connection after that.
    Nothing about the resulting run says so: it is a findings file with nothing in
    it, which reads as a scanner with no coverage.

    Any HTTP status counts as reachable, 401 and 500 included: the question is
    whether something is listening, not whether it likes us. Only a transport
    failure is a failure.

    This runs BEFORE the run is opened, so the collector drops the event and the tool
    is not credited with our probe. If this check ever moves after the run opens,
    that stops being true.
    """
    name = f"reachable:{app.key}"
    if not app.base_url:
        return Check(name, False, "no tool-facing base URL to reach")
    res = http.request("GET", app.base_url + "/", timeout=15, connect_to=address)
    if res.status == 0:
        return Check(
            name,
            False,
            f"{app.base_url} did not answer from the tool's network ({res.error}). "
            "A base URL that resolves but refuses connections -- naming the wrong "
            "port, most often -- produces a whole run of connection errors that reads "
            "as a scanner with no coverage.",
        )
    return Check(name, True, f"{app.base_url} answered HTTP {res.status}")


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
    target_http: Any = None,
    allow_pull: bool = True,
    check_dns: bool = True,
    allow_stale_credentials: bool = False,
) -> PreflightReport:
    report = PreflightReport()
    authed_apps = apps_with_authenticated_entrypoints()

    report.checks.append(check_no_dev_services(docker))
    report.checks.append(check_no_published_ports(docker, config.network))

    for app in apps:
        # Ask the target first: the answer changes what check_credentials reads.
        if config.target_file(app) is not None and app.reset_service:
            report.checks.append(
                check_live_credentials(
                    config,
                    app,
                    docker,
                    # A stale file only corrupts the published result for an
                    # application that actually has a login to get wrong.
                    fatal_on_mismatch=app.key in authed_apps and not allow_stale_credentials,
                )
            )
        report.checks.extend(check_credentials(config, app, authed_apps))
        report.checks.append(
            check_internal_name_is_platform_side(
                docker, app, (config.network, app.internal_network)
            )
        )
        report.checks.append(
            check_name_ambiguity(docker, app, (config.network, app.internal_network))
        )
        entry = (topology or {}).get(app.key)
        addresses = list(getattr(entry, "addresses", entry) or [])
        public_address = (
            entry.address_on(config.network, prefer_host=app.host.split(":")[0])
            if hasattr(entry, "address_on")
            else None
        )
        if check_dns and tool_image:
            report.checks.append(
                check_dns_from_tool_network(
                    docker, app, tool_image, addresses, network=config.network,
                    allow_pull=allow_pull,
                )
            )
        if target_http is not None:
            report.checks.append(check_base_url_reachable(target_http, app, public_address))
    for check in report.checks:
        if check.indeterminate:
            log.warning("preflight %s could not be verified: %s", check.name, check.detail)
        elif not check.ok:
            log.error("preflight %s failed: %s", check.name, check.detail)
    return report
