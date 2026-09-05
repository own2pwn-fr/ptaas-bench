"""Harness configuration: what the targets are, what the tools are, who logs in.

Three sources, loaded from ``runners/`` unless overridden:

* ``apps.yaml``   -- the target registry (compose services, the two URLs, reset).
* ``tools.yaml``  -- the tool registry (image, pinned digest, profiles, preparation).
* credentials     -- ``targets/<app>/bench-credentials.yaml`` in the shape fixed by
  targets/target-contract.yaml, with ``runners/credentials.yaml`` as a local
  override for targets that have not landed yet.

THE TARGET OWNS ITS OWN ADDRESS
A target's customer-facing hostname and its seeded content are derived per deployment
from DEPLOY_SEED, so a URL written into apps.yaml is correct for exactly one
deployment and silently wrong for the next -- and "wrong" here looks like the tool
simply finding nothing. The authority is therefore ``targets/<app>/bench-credentials.yaml``,
which the target generates at seed time from the same seed and which cannot drift
from what the application actually answers on. apps.yaml keeps what genuinely belongs
to the harness: compose services, how to reset, what to restart, which internal name
our own traffic uses.

TWO URLS PER TARGET, AND THE DIFFERENCE MATTERS
The tool under test reaches a target by its customer-facing name on the sealed
``bench-public`` network. The harness reaches the same target by its internal name,
from inside the platform's own address range, because the collector and the target
SDK classify traffic as synthetic *by source address* -- a header would be visible to
a tool through any reflection or verbose error and would hand it the shape of the
grader. Log in over the wrong one and the harness's own requests are scored as the
tool's crawl coverage.

DECEPTION IS A CONFIGURATION PROPERTY
Nothing this file produces may tell a tool it is being graded: no benchmark
vocabulary in a URL, a header, a user-agent or a mount path. See
targets/target-contract.yaml; the forbidden-string list is asserted by
runners/tests/test_deception.py rather than left to good intentions.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import yaml

RUNNERS_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = RUNNERS_DIR.parent
TARGETS_DIR = REPO_ROOT / "targets"

# The tool under test is attached here and to nothing else, and this network is
# `internal: true`: no route out, with the sinkhole as its resolver. Hard-coded
# rather than configurable, because making it easy to change is making it easy to
# accidentally give a scanner a route to the collector or to the internet.
PUBLIC_NETWORK = "bench-public"

# Preparation steps (template and signature updates) run here instead: they need the
# internet, which the tool network does not have, and they run before the run opens.
PREP_NETWORK_DEFAULT = "bridge"

# Where a run directory is mounted inside a tool container. Deliberately anonymous:
# an agentic tool reads its own filesystem, and /proc/self/mountinfo shows it.
CONTAINER_WORKDIR = "/work"

# `${VAR}` and `${VAR:-default}`, the same spelling docker compose uses, so a target's
# compose fragment and this file can share an environment variable verbatim.
_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


class ConfigError(RuntimeError):
    pass


def _expand(value: Any) -> Any:
    """Expand ``${VAR}`` / ``${VAR:-default}`` from the environment inside strings."""
    if isinstance(value, str):
        return _VAR_RE.sub(lambda m: os.environ.get(m.group(1)) or (m.group(2) or ""), value)
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand(v) for v in value]
    return value


def _strip_default_port(url: str) -> str:
    """`http://host:80/x` -> `http://host/x` (and :443 for https).

    ZAP documents explicitly that a context URL spelling out the default port matches
    nothing, and every tool's scope check compares the string form, so one canonical
    spelling is safer everywhere.
    """
    parts = urlsplit(url)
    defaults = {"http": ":80", "https": ":443"}
    suffix = defaults.get(parts.scheme)
    if suffix and parts.netloc.endswith(suffix):
        netloc = parts.netloc[: -len(suffix)]
        return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    return url


@dataclass
class Credentials:
    """Tool-neutral description of how to log into one application.

    Deliberately not written in any tool's dialect: half the corpus sits behind a
    login, so every driver has to authenticate, and one description translated per
    tool is the only way to be sure they were all given the same thing. A per-tool
    credentials file drifts, and the drift shows up as a capability difference.
    """

    app: str
    role: str = "user"
    # form: HTML form POST. json: JSON body POST. basic: HTTP basic. bearer: a token
    # obtained from a json login and replayed as a header.
    kind: str = "form"
    login_url: str | None = None
    login_page_url: str | None = None
    # The same locations as paths. The harness resolves them against the target's
    # internal name (so its traffic is classified as the platform's own) while the
    # tool must resolve them against the customer-facing name -- the internal alias
    # does not exist on the tool's network, and a driver handed our URL would fail to
    # log in and scan anonymously while still being reported as authenticated.
    login_path: str | None = None
    login_page_path: str | None = None
    verify_path: str | None = None
    username: str = ""
    password: str = ""
    username_field: str = "username"
    password_field: str = "password"
    extra_fields: dict[str, str] = field(default_factory=dict)
    # How the harness (and the tools that cannot log in by themselves) recognise a
    # session that is still valid. `logged_in_regex` is also what ZAP needs.
    logged_in_regex: str | None = None
    logged_out_regex: str | None = None
    verify_url: str | None = None
    # Where the token lives in the login response, dotted, for `kind: bearer`.
    token_json_path: str | None = None
    session: str = "cookie"  # cookie | bearer | header
    session_cookie: str | None = None
    header_name: str = "Authorization"
    header_template: str = "Bearer {token}"
    # Paths a scanner must never touch or it logs itself out mid-scan. The single
    # most common cause of an authenticated scan silently degrading to an anonymous
    # one, which then reads as the tool being blind to half the corpus.
    logout_paths: list[str] = field(default_factory=list)
    subject_id: str | None = None

    @classmethod
    def from_dict(cls, app: str, data: dict[str, Any]) -> Credentials:
        data = _expand(dict(data))
        known = set(cls.__dataclass_fields__)  # type: ignore[attr-defined]
        unknown = set(data) - known - {"app"}
        if unknown:
            raise ConfigError(f"credentials for {app!r}: unknown keys {sorted(unknown)}")
        return cls(app=app, **data)

    @classmethod
    def from_target_file(
        cls, path: Path, *, role: str = "user", base_url_override: str | None = None
    ) -> Credentials:  # noqa: D401 - thin wrapper kept for call sites
        """Load ``targets/<app>/bench-credentials.yaml`` (target-contract.yaml shape).

        The target owns this file, so the harness reads it rather than asking an
        operator to copy the values into a second place where they can go stale.
        ``base_url_override`` is how harness traffic is pointed at the target's
        internal name: the file states the customer-facing URL, which is the tool's
        route, not ours.
        """
        doc = _expand(yaml.safe_load(path.read_text(encoding="utf-8")) or {})
        return cls.from_target_doc(doc, path, role=role, base_url_override=base_url_override)

    @classmethod
    def from_target_doc(
        cls,
        doc: dict[str, Any],
        path: Path,
        *,
        role: str = "user",
        base_url_override: str | None = None,
    ) -> Credentials:
        app = str(doc.get("app") or path.parent.name)
        login = doc.get("login") or {}
        session = doc.get("session") or {}
        users = doc.get("users") or []

        chosen = next((u for u in users if str(u.get("role")) == role), None)
        if chosen is None:
            roles = sorted({str(u.get("role")) for u in users})
            raise ConfigError(f"{path}: no user with role {role!r} (have {roles})")

        base = (base_url_override or doc.get("base_url") or "").rstrip("/")
        login_path = str(login.get("url") or "")
        login_url = login_path if login_path.startswith("http") else f"{base}{login_path}"

        kind_map = {"json": "json", "form": "form", "basic": "basic", "urlencoded": "form"}
        kind = kind_map.get(str(login.get("type", "form")).lower(), "form")
        session_kind = str(session.get("kind", "cookie")).lower()

        # The contract's indicators are substrings, not regexes: a literal that
        # happens to contain a regex metacharacter must not silently change meaning.
        logged_in = login.get("logged_in_indicator")
        logged_out = login.get("logged_out_indicator")

        page_path = login.get("page_url") or (login_path if kind == "form" else None)
        return cls(
            app=app,
            role=role,
            kind=kind,
            login_url=login_url,
            login_path=login_path or None,
            login_page_path=str(page_path) if page_path else None,
            verify_path=str(login.get("verify_url")) if login.get("verify_url") else None,
            # The contract does not carry a separate page URL. For a form login the
            # same path served over GET is the login page, which is what a browser
            # fetches first and where a CSRF cookie is minted.
            login_page_url=_resolve(base, login.get("page_url")) or (
                login_url if kind == "form" else None
            ),
            username=str(chosen.get("username", "")),
            password=str(chosen.get("password", "")),
            username_field=str(login.get("username_field", "username")),
            password_field=str(login.get("password_field", "password")),
            logged_in_regex=re.escape(str(logged_in)) if logged_in else None,
            logged_out_regex=re.escape(str(logged_out)) if logged_out else None,
            verify_url=_resolve(base, login.get("verify_url")),
            token_json_path=login.get("token_json_path"),
            session="bearer" if session_kind in ("bearer", "header") else "cookie",
            session_cookie=session.get("name"),
            header_name=str(session.get("header", "Authorization")),
            logout_paths=[str(p) for p in (login.get("exclude") or [])],
            subject_id=str(chosen.get("subject_id")) if chosen.get("subject_id") else None,
        )

    def for_base(self, base_url: str) -> Credentials:
        """The same credentials with their URLs resolved against another base.

        Used to hand a driver the tool-facing login while the harness keeps the
        internal one. Credentials that carry no paths (a hand-written override with
        absolute URLs) are returned unchanged rather than rewritten blindly.
        """
        if not self.login_path:
            return self
        base = base_url.rstrip("/")
        return replace(
            self,
            login_url=_resolve(base, self.login_path),
            login_page_url=_resolve(base, self.login_page_path),
            verify_url=_resolve(base, self.verify_path),
        )

    def logout_regexes(self) -> list[str]:
        """Excluded paths as regexes, with route templates expanded.

        The contract writes them as route templates -- `/api/account/sessions/{id}` --
        and a template is not a regex: `{id}` is an invalid repetition in Java, which
        is what ZAP compiles with, so a context carrying it verbatim is rejected and
        the whole plan fails to load. Expanded to a single path segment instead.
        """
        out = []
        for path in self.logout_paths:
            pattern = "".join(
                "[^/]+" if part.startswith("{") and part.endswith("}") else re.escape(part)
                for part in re.split(r"(\{[^}]+\})", path)
                if part
            )
            out.append(f".*{pattern}.*")
        return out

    def logout_prefixes(self) -> list[str]:
        """Excluded paths as literal prefixes, for tools that match on substrings.

        Truncated at the first template segment: `/api/account/sessions/{id}` becomes
        `/api/account/sessions`. That excludes a little more than asked, which is the
        right direction -- the cost of over-excluding is a missed endpoint, the cost
        of under-excluding is a scanner deleting its own session mid-run and finishing
        the scan anonymously while still being reported as authenticated.
        """
        return [path.split("{", 1)[0].rstrip("/") or "/" for path in self.logout_paths]

    def login_body(self) -> dict[str, str]:
        body = {self.username_field: self.username, self.password_field: self.password}
        body.update(self.extra_fields)
        return body


def _resolve(base: str, path: Any) -> str | None:
    if not path:
        return None
    text = str(path)
    return text if text.startswith("http") else f"{base}{text}"


@dataclass
class TargetCredentialsFile:
    """``targets/<app>/bench-credentials.yaml``, as written by the target.

    Three things come from here and nowhere else: the base URL the application
    actually answers on (seed-derived, so it cannot be hardcoded), the shape of its
    login, and the seeded identities. The file is also how a target says it has *no*
    login: the contract asks for an empty ``users:`` list with a comment, precisely so
    that "this application has no authentication" can be told apart from "someone
    forgot to write the file". Only the second is a reason to stop a run.
    """

    path: Path
    app: str
    base_url: str | None
    roles: list[str]
    raw: dict[str, Any]

    @property
    def declares_no_login(self) -> bool:
        return not self.roles

    @classmethod
    def load(cls, path: Path) -> TargetCredentialsFile:
        doc = _expand(yaml.safe_load(path.read_text(encoding="utf-8")) or {})
        if not isinstance(doc, dict):
            raise ConfigError(f"{path}: expected a mapping at the top level")
        users = doc.get("users") or []
        return cls(
            path=path,
            app=str(doc.get("app") or path.parent.name),
            base_url=_strip_default_port(str(doc["base_url"]).rstrip("/")) if doc.get("base_url") else None,
            roles=[str(u.get("role")) for u in users if u.get("role")],
            raw=doc,
        )

    def for_role(self, role: str, *, base_url_override: str | None = None) -> Credentials | None:
        if self.declares_no_login:
            return None
        return Credentials.from_target_doc(
            self.raw, self.path, role=role, base_url_override=base_url_override
        )

    def merged_with(self, emitted: dict[str, Any]) -> TargetCredentialsFile:
        """Overlay the identities the target just printed for the deployment in front of us.

        The committed file belongs to one DEPLOY_SEED. Where the running target
        disagrees with it, the running target is right: e-mail addresses and
        passwords move with the seed, while the login shape and the subject ids do
        not. Only the blocks the target emits are replaced.
        """
        raw = dict(self.raw)
        for key in ("app", "base_url", "users"):
            if emitted.get(key) is not None:
                raw[key] = emitted[key]
        return replace(
            self,
            raw=raw,
            roles=[str(u.get("role")) for u in (raw.get("users") or []) if u.get("role")],
            base_url=(
                _strip_default_port(str(raw["base_url"]).rstrip("/"))
                if raw.get("base_url")
                else self.base_url
            ),
        )


@dataclass
class AppSpec:
    """One target application, as the harness needs to see it."""

    key: str
    services: list[str] = field(default_factory=list)
    # What the TOOL uses: the customer-facing name on bench-public. Normally left
    # unset here and resolved from targets/<app>/bench-credentials.yaml, which the
    # target generates from DEPLOY_SEED. A value written here is a fallback for a
    # target that has not landed yet, and is overridden the moment its file exists.
    base_url: str = ""
    # What the HARNESS uses: the same application by its internal name, so that the
    # platform's own traffic arrives from the platform's address range and is
    # classified synthetic. Falls back to base_url, loudly.
    internal_url: str | None = None
    # The network the harness's own traffic must arrive over, because that is what
    # decides whether the target records it as the platform's own or as the tool's.
    internal_network: str = "bench-internal"
    # Reset, per targets/target-contract.yaml: a command inside a container, never a
    # route. `reset_service` is the compose service that owns the state.
    reset_service: str | None = None
    reset_command: str = "/usr/local/bin/state-reset"
    reset_timeout_s: float = 600.0
    # Restarted before the reset command runs. DEFAULT: every service of the app.
    #
    # A reset command computes its digest over persistent storage, so it is blind to
    # state held in the process: a write to Object.prototype (shopfront plants two),
    # an in-memory cache, a compiled template cache, a cached object in Varnish. The
    # digest comes back identical and the next tool inherits the previous tool's
    # exploit -- which is the single most direct way one scanner's work becomes
    # another's score, and exactly what the reset verification exists to prevent.
    #
    # So the default is on and a target opts out with `restart_services: []`. The
    # cost of a needless restart is seconds; the cost of a missed one is a published
    # number attributing one tool's exploit to another.
    restart_services: list[str] | None = None
    # Waited on after a restart. Defaults to whatever was restarted.
    health_services: list[str] = field(default_factory=list)
    # Pin the expected seeded digest here to check every run against a fixed value
    # instead of against whatever the previous run recorded.
    expected_digest: str | None = None
    openapi_url: str | None = None
    graphql_url: str | None = None
    include_paths: list[str] = field(default_factory=list)
    exclude_paths: list[str] = field(default_factory=list)
    credentials_file: str | None = None
    routes_file: str | None = None
    notes: str | None = None

    def __post_init__(self) -> None:
        self.base_url = _strip_default_port(self.base_url)
        if self.internal_url:
            self.internal_url = _strip_default_port(self.internal_url)
        if not self.services:
            self.services = [self.key]
        if self.reset_service is None:
            self.reset_service = self.services[0]
        if not self.health_services:
            self.health_services = list(self.services_to_restart)

    @classmethod
    def from_dict(cls, key: str, data: dict[str, Any]) -> AppSpec:
        data = _expand(dict(data))
        known = set(cls.__dataclass_fields__)  # type: ignore[attr-defined]
        unknown = set(data) - known - {"key"}
        if unknown:
            raise ConfigError(f"app {key!r}: unknown keys {sorted(unknown)}")
        return cls(key=key, **data)

    @property
    def services_to_restart(self) -> list[str]:
        """Every service unless the target explicitly opted out with an empty list."""
        return list(self.services) if self.restart_services is None else list(self.restart_services)

    @property
    def restart_opted_out(self) -> bool:
        return self.restart_services == []

    @property
    def harness_url(self) -> str:
        """Base URL the harness itself uses. See the class docstring."""
        return self.internal_url or self.base_url

    @property
    def harness_url_is_the_tools(self) -> bool:
        """True when our traffic is indistinguishable from the tool's by name alone.

        Either no internal name was configured, or -- as with a target that answers
        to the same alias on both networks -- it is the same name the tool uses. In
        both cases the interface, and therefore the classification, is decided by
        whichever address the resolver happens to return, so the orchestrator pins
        the connection to the internal address instead.
        """
        return not self.internal_url or self.internal_url == self.base_url

    @property
    def internal_host(self) -> str:
        return urlsplit(self.harness_url).hostname or ""

    @property
    def host(self) -> str:
        return urlsplit(self.base_url).netloc

    def default_credentials_file(self) -> Path:
        if self.credentials_file:
            path = Path(self.credentials_file)
            return path if path.is_absolute() else REPO_ROOT / path
        return TARGETS_DIR / self.key / "bench-credentials.yaml"

    def default_routes_file(self) -> Path:
        if self.routes_file:
            path = Path(self.routes_file)
            return path if path.is_absolute() else REPO_ROOT / path
        return TARGETS_DIR / self.key / "routes.yaml"


@dataclass
class ToolSpec:
    """A tool's image and profiles. Pinning a digest here pins a published number."""

    key: str
    image: str
    digest: str | None = None
    # {context, dockerfile, args} for the tools with no usable published image.
    build: dict[str, Any] | None = None
    default_profile: str = "default"
    profiles: list[str] = field(default_factory=lambda: ["default"])
    # Network for the preparation step (template/signature updates). It needs the
    # internet; the tool network deliberately has none.
    prep_network: str = PREP_NETWORK_DEFAULT
    notes: str | None = None

    @property
    def image_ref(self) -> str:
        """What is actually passed to ``docker run``.

        When a digest is pinned it wins over the tag: `:stable` moves, `@sha256:...`
        does not, and a benchmark table that cannot be reproduced is an opinion.
        """
        if not self.digest:
            return self.image
        base = self.image.split("@", 1)[0]
        name, sep, tag = base.rpartition(":")
        # `registry:5000/image` has a colon that is a port, not a tag.
        if sep and "/" not in tag:
            base = name
        return f"{base}@{self.digest}"

    @classmethod
    def from_dict(cls, key: str, data: dict[str, Any]) -> ToolSpec:
        data = _expand(dict(data))
        known = set(cls.__dataclass_fields__)  # type: ignore[attr-defined]
        unknown = set(data) - known - {"key"}
        if unknown:
            raise ConfigError(f"tool {key!r}: unknown keys {sorted(unknown)}")
        return cls(key=key, **data)


@dataclass
class BenchConfig:
    apps: dict[str, AppSpec]
    tools: dict[str, ToolSpec]
    credentials_override: dict[str, Credentials]
    # app -> the `users:`/`base_url:` block the target itself printed at preflight.
    emitted_credentials: dict[str, dict[str, Any]] = field(default_factory=dict)
    compose_file: Path | None = None
    compose_project: str = "platform-edge"
    # The service whose loopback the control plane is reachable on. Run management
    # and event export answer 404 to every source but the collector's own loopback
    # and the sinkhole, so the orchestrator exec's into the collector and talks to
    # 127.0.0.1. Nothing is published to the host.
    collector_service: str = "otel-collector"
    collector_url: str = "http://127.0.0.1:8900"
    # The dual-homed sinkhole. Harness traffic aimed at a target goes through it,
    # because it sits in the address range both the collector and the target SDKs
    # treat as synthetic.
    platform_client_service: str = "resolver"
    network: str = PUBLIC_NETWORK
    results_dir: Path = REPO_ROOT / "results" / "runs"

    @classmethod
    def load(
        cls,
        *,
        apps_path: Path | None = None,
        tools_path: Path | None = None,
        credentials_path: Path | None = None,
    ) -> BenchConfig:
        apps_doc = _read_yaml(apps_path or RUNNERS_DIR / "apps.yaml")
        tools_doc = _read_yaml(tools_path or RUNNERS_DIR / "tools.yaml")

        creds_path = credentials_path or RUNNERS_DIR / "credentials.yaml"
        creds_doc: dict[str, Any] = {}
        if creds_path.exists():
            creds_doc = _read_yaml(creds_path)

        platform = apps_doc.get("platform") or {}
        compose_file = platform.get("compose_file")
        return cls(
            apps={k: AppSpec.from_dict(k, v or {}) for k, v in (apps_doc.get("apps") or {}).items()},
            tools={k: ToolSpec.from_dict(k, v or {}) for k, v in (tools_doc.get("tools") or {}).items()},
            credentials_override={
                k: Credentials.from_dict(k, v or {}) for k, v in (creds_doc.get("apps") or {}).items()
            },
            compose_file=Path(compose_file) if compose_file else (REPO_ROOT / "docker-compose.yml"),
            compose_project=platform.get("compose_project", "platform-edge"),
            collector_service=platform.get("collector_service", "otel-collector"),
            collector_url=platform.get("collector_url", "http://127.0.0.1:8900"),
            platform_client_service=platform.get("platform_client_service", "resolver"),
            network=platform.get("network", PUBLIC_NETWORK),
            results_dir=Path(platform.get("results_dir", REPO_ROOT / "results" / "runs")),
        )

    def select_apps(self, keys: list[str] | None) -> list[AppSpec]:
        if not keys:
            return list(self.apps.values())
        missing = [k for k in keys if k not in self.apps]
        if missing:
            raise ConfigError(f"unknown app(s) {missing}; known: {sorted(self.apps)}")
        return [self.apps[k] for k in keys]

    def target_file(self, app: AppSpec) -> TargetCredentialsFile | None:
        path = app.default_credentials_file()
        if not path.exists():
            return None
        target = TargetCredentialsFile.load(path)
        emitted = self.emitted_credentials.get(app.key)
        return target.merged_with(emitted) if emitted else target

    def resolve_urls(self) -> dict[str, str]:
        """Take each target's base URL from the file the target itself writes.

        Returns the apps whose URL came from a fallback in apps.yaml rather than from
        the target, so the orchestrator can say so: that URL is correct for exactly
        one deployment, and when it is wrong the run looks like a tool with poor
        coverage rather than like a misconfiguration.
        """
        fallbacks: dict[str, str] = {}
        for app in self.apps.values():
            target = self.target_file(app)
            if target is not None and target.base_url:
                app.base_url = target.base_url
            elif app.base_url:
                fallbacks[app.key] = app.base_url
        return fallbacks

    def creds_for(self, app: AppSpec, *, role: str = "user") -> Credentials | None:
        """The target's own credentials file, or a local override.

        The override exists for applications that have not landed yet; when the
        target ships its file, that file wins, because it is maintained next to the
        login it describes. A file that declares an empty ``users:`` list is a target
        saying it has no login, which is a legitimate answer and not an error.
        """
        target = self.target_file(app)
        if target is not None:
            return target.for_role(role, base_url_override=app.harness_url)
        return self.credentials_override.get(app.key)


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"missing configuration file: {path}")
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(doc, dict):
        raise ConfigError(f"{path} must contain a mapping at the top level")
    return doc
