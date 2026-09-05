"""Harness configuration: what the targets are, what the tools are, who logs in.

Three files, loaded from ``runners/`` unless overridden:

* ``apps.yaml``        -- the target registry (compose services, URLs, control plane).
* ``tools.yaml``       -- the tool registry (image, pinned digest, profiles).
* ``credentials.yaml`` -- per-application credentials, tool-neutral (git-ignored).

The credentials file is deliberately *not* written in any tool's dialect. Half the
corpus is behind a login, so every driver has to authenticate; if the file were in
ZAP's dialect, each new driver would need its own copy and they would drift, which
is the classic way a benchmark ends up comparing an authenticated scan against an
anonymous one and calling the difference "detection capability". One neutral
description, translated per tool at run time.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import yaml

RUNNERS_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = RUNNERS_DIR.parent

# The tool under test is attached to this network and to nothing else
# (see docker-compose.yml). Hard-coded rather than configurable: making it easy to
# change is making it easy to accidentally give a scanner a route to the collector.
PUBLIC_NETWORK = "bench-public"


class ConfigError(RuntimeError):
    pass


def _expand(value: Any) -> Any:
    """Expand ``${VAR}`` from the environment inside strings.

    Credentials belong in the environment or a secret store on shared machines; the
    YAML is then safe to keep next to the rest of the configuration.
    """
    if isinstance(value, str):
        return os.path.expandvars(value)
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand(v) for v in value]
    return value


@dataclass
class Credentials:
    """Tool-neutral description of how to log into one application."""

    app: str
    # form: HTML form POST. json: JSON body POST. basic: HTTP basic. bearer: a
    # token obtained from a json login and sent as a header.
    kind: str = "form"
    login_url: str | None = None
    login_page_url: str | None = None
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
    header_name: str = "Authorization"
    header_template: str = "Bearer {token}"
    # Paths a scanner must never touch or it logs itself out mid-scan. Every driver
    # is expected to honour these; it is the single most common cause of an
    # authenticated scan silently degrading into an anonymous one.
    logout_paths: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, app: str, data: dict[str, Any]) -> Credentials:
        data = _expand(dict(data))
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        unknown = set(data) - known - {"app"}
        if unknown:
            raise ConfigError(f"credentials for {app!r}: unknown keys {sorted(unknown)}")
        return cls(app=app, **data)

    def login_body(self) -> dict[str, str]:
        body = {self.username_field: self.username, self.password_field: self.password}
        body.update(self.extra_fields)
        return body


@dataclass
class AppSpec:
    """One target application, as the harness needs to see it."""

    key: str
    services: list[str]
    base_url: str
    health_path: str = "/healthz"
    seed_path: str = "/__bench__/seed"
    state_path: str = "/__bench__/state"
    # Control-plane URLs default to base_url, but a target that serves its control
    # plane on a separate internal-only port (which it should) overrides this.
    control_url: str | None = None
    control_token: str | None = None
    openapi_url: str | None = None
    graphql_url: str | None = None
    include_paths: list[str] = field(default_factory=list)
    exclude_paths: list[str] = field(default_factory=list)
    profile: str | None = None
    notes: str | None = None

    def __post_init__(self) -> None:
        # Strip a redundant default port. ZAP documents explicitly that a context URL
        # of `http://host:80` fails to match anything, and every tool's scope check
        # compares the string form, so one canonical spelling is safer everywhere.
        self.base_url = _strip_default_port(self.base_url)
        if self.control_url:
            self.control_url = _strip_default_port(self.control_url)

    @classmethod
    def from_dict(cls, key: str, data: dict[str, Any]) -> AppSpec:
        data = _expand(dict(data))
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        unknown = set(data) - known - {"key"}
        if unknown:
            raise ConfigError(f"app {key!r}: unknown keys {sorted(unknown)}")
        if "base_url" not in data:
            raise ConfigError(f"app {key!r}: base_url is required")
        data.setdefault("services", [key])
        return cls(key=key, **data)

    # -- derived URLs -----------------------------------------------------------

    @property
    def _control_base(self) -> str:
        return (self.control_url or self.base_url).rstrip("/")

    @property
    def health_url(self) -> str:
        return f"{self._control_base}{self.health_path}"

    @property
    def seed_url(self) -> str:
        return f"{self._control_base}{self.seed_path}"

    @property
    def state_url(self) -> str:
        return f"{self._control_base}{self.state_path}"

    @property
    def control_headers(self) -> dict[str, str]:
        # X-Bench-Selftest makes the target SDK flag these requests as synthetic, so
        # the platform's own traffic is never scored as the tool's crawl coverage.
        headers = {"X-Bench-Selftest": "1"}
        if self.control_token:
            headers["X-Bench-Token"] = self.control_token
        return headers

    @property
    def host(self) -> str:
        return urlsplit(self.base_url).netloc


@dataclass
class ToolSpec:
    """A tool's image and profiles. Pinning a digest here pins a published number."""

    key: str
    image: str
    digest: str | None = None
    # {context, dockerfile, args} for the two tools with no usable published image.
    build: dict[str, Any] | None = None
    default_profile: str = "default"
    profiles: list[str] = field(default_factory=lambda: ["default"])
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
        # `registry:5000/image` has a colon that is a port, not a tag: only strip
        # the last component when it cannot contain a slash.
        if sep and "/" not in tag:
            base = name
        return f"{base}@{self.digest}"

    @classmethod
    def from_dict(cls, key: str, data: dict[str, Any]) -> ToolSpec:
        data = _expand(dict(data))
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        unknown = set(data) - known - {"key"}
        if unknown:
            raise ConfigError(f"tool {key!r}: unknown keys {sorted(unknown)}")
        return cls(key=key, **data)


@dataclass
class BenchConfig:
    apps: dict[str, AppSpec]
    tools: dict[str, ToolSpec]
    credentials: dict[str, Credentials]
    compose_file: Path | None = None
    compose_project: str = "ptaas-bench"
    collector_service: str = "collector"
    collector_url: str = "http://collector:8900"
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
        # Absent credentials is legal (the anonymous half of the corpus still scans)
        # but it is never silent: the orchestrator warns per app in scope.

        platform = apps_doc.get("platform") or {}
        compose_file = platform.get("compose_file")
        return cls(
            apps={k: AppSpec.from_dict(k, v or {}) for k, v in (apps_doc.get("apps") or {}).items()},
            tools={k: ToolSpec.from_dict(k, v or {}) for k, v in (tools_doc.get("tools") or {}).items()},
            credentials={
                k: Credentials.from_dict(k, v or {}) for k, v in (creds_doc.get("apps") or {}).items()
            },
            compose_file=Path(compose_file) if compose_file else (REPO_ROOT / "docker-compose.yml"),
            compose_project=platform.get("compose_project", "ptaas-bench"),
            collector_service=platform.get("collector_service", "collector"),
            collector_url=platform.get("collector_url", "http://collector:8900"),
            network=platform.get("network", PUBLIC_NETWORK),
            results_dir=Path(platform.get("results_dir", REPO_ROOT / "results" / "runs")),
        )

    def select_apps(self, keys: Iterable[str] | None) -> list[AppSpec]:
        if not keys:
            return list(self.apps.values())
        missing = [k for k in keys if k not in self.apps]
        if missing:
            raise ConfigError(f"unknown app(s) {missing}; known: {sorted(self.apps)}")
        return [self.apps[k] for k in keys]

    def creds_for(self, app: str) -> Credentials | None:
        return self.credentials.get(app)


def _strip_default_port(url: str) -> str:
    """`http://host:80/x` -> `http://host/x` (and :443 for https)."""
    parts = urlsplit(url)
    defaults = {"http": ":80", "https": ":443"}
    suffix = defaults.get(parts.scheme)
    if suffix and parts.netloc.endswith(suffix):
        netloc = parts.netloc[: -len(suffix)]
        return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    return url


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"missing configuration file: {path}")
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(doc, dict):
        raise ConfigError(f"{path} must contain a mapping at the top level")
    return doc
