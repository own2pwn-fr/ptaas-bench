"""Where everything lives, read once from the environment.

The agent runs beside the estate rather than inside any of it: it holds the deployment
routine that lays down the sites, and it reads what the servers and the datastores
already write about themselves. Nothing here is a secret; the values are paths and
addresses that appear in the same form in the deployment notes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str) -> str:
    value = os.environ.get(name, "").strip()
    return value or default


def _tuple(name: str, default: str = "") -> tuple[str, ...]:
    raw = _env(name, default)
    return tuple(item.strip() for item in raw.split(",") if item.strip())


@dataclass(frozen=True)
class Settings:
    # -- the sites ----------------------------------------------------------
    sites_root: str = field(default_factory=lambda: _env("SITES_ROOT", "/srv/sites"))
    private_root: str = field(default_factory=lambda: _env("PRIVATE_ROOT", "/srv/private"))
    access_log: str = field(default_factory=lambda: _env("ACCESS_LOG", "/var/log/site/access.log"))
    site_base_url: str = field(default_factory=lambda: _env("SITE_BASE_URL", "http://infra-web"))

    # -- the datastores -----------------------------------------------------
    cache_host: str = field(default_factory=lambda: _env("CACHE_HOST", "infra-redis"))
    cache_port: int = field(default_factory=lambda: int(_env("CACHE_PORT", "6379")))
    # The name each store is reported under. It is the store's deployment name rather
    # than the address a particular container holds, so a record says which store was
    # spoken to even when the estate is re-addressed.
    cache_label: str = field(default_factory=lambda: _env("CACHE_LABEL", "infra-redis:6379"))
    queue_label: str = field(default_factory=lambda: _env("QUEUE_LABEL", "infra-redis-ops:6380"))
    records_label: str = field(default_factory=lambda: _env("RECORDS_LABEL", "infra-mongo:27017"))
    queue_host: str = field(default_factory=lambda: _env("QUEUE_HOST", "infra-redis-ops"))
    queue_port: int = field(default_factory=lambda: int(_env("QUEUE_PORT", "6380")))
    records_host: str = field(default_factory=lambda: _env("RECORDS_HOST", "infra-mongo"))
    records_port: int = field(default_factory=lambda: int(_env("RECORDS_PORT", "27017")))
    records_db: str = field(default_factory=lambda: _env("RECORDS_DB", "nlf_records"))
    search_base: str = field(default_factory=lambda: _env("SEARCH_BASE", "http://infra-elastic:9200"))
    search_log_dir: str = field(default_factory=lambda: _env("SEARCH_LOG_DIR", "/var/log/search"))
    search_index: str = field(default_factory=lambda: _env("SEARCH_INDEX", "nlf-enquiries"))
    search_notes_index: str = field(
        default_factory=lambda: _env("SEARCH_NOTES_INDEX", "nlf-delivery-notes"))

    # Stores that carry a password. They are loaded like any other client, which is the
    # only reason their passwords are here.
    sessions_host: str = field(default_factory=lambda: _env("SESSIONS_HOST", "infra-sessions"))
    sessions_port: int = field(default_factory=lambda: int(_env("SESSIONS_PORT", "6379")))
    sessions_password: str = field(default_factory=lambda: _env("SESSIONS_PASSWORD", ""))
    jobs_host: str = field(default_factory=lambda: _env("JOBS_HOST", "infra-jobs"))
    jobs_port: int = field(default_factory=lambda: int(_env("JOBS_PORT", "6379")))
    jobs_password: str = field(default_factory=lambda: _env("JOBS_PASSWORD", ""))

    # -- operations ---------------------------------------------------------
    control_addr: str = field(default_factory=lambda: _env("CONTROL_ADDR", "0.0.0.0:9902"))
    # Address ranges that belong to the estate's own tooling. Anything arriving from
    # them is our own deployment traffic and is recorded as generated rather than
    # organic. Decided on the socket peer address and on nothing else.
    synthetic_cidrs: tuple[str, ...] = field(
        default_factory=lambda: _tuple("TELEMETRY_SYNTHETIC_CIDRS", "10.77.0.0/24"))
    site_domain: str = field(default_factory=lambda: _env("INFRA_SITE_DOMAIN", "northlakefab.com"))
    deploy_seed: str = field(default_factory=lambda: os.environ.get("DEPLOY_SEED", ""))
    poll_interval: float = field(default_factory=lambda: float(_env("POLL_INTERVAL_MS", "250")) / 1000.0)


settings = Settings()
