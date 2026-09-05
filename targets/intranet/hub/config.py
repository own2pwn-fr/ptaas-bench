"""Runtime configuration for the staff services application.

Everything comes from the environment with a working default, so a developer can run
`python -m hub` against a scratch database and exercise the same code paths the
deployment runs.
"""

from __future__ import annotations

import os


def _int(raw: str | None, fallback: int) -> int:
    try:
        return int(str(raw))
    except (TypeError, ValueError):
        return fallback


class Settings:
    def __init__(self) -> None:
        # Where the staff directory, the requests and the equipment register live.
        self.database_path = os.environ.get("HUB_DATABASE", "/var/lib/hub/hub.sqlite3")
        # Flat operational logs. Payroll reads the approvals file directly, which is
        # why it is still text rather than rows.
        self.log_dir = os.environ.get("HUB_LOG_DIR", "/var/lib/hub/log")

        # Content generation. Two deployments of the same release must not look alike
        # in the directory or the equipment register: the staging estate, the training
        # estate and the induction estate all run this image with a different value.
        self.deploy_seed = os.environ.get("DEPLOY_SEED", "lh-1")

        self.site_domain = os.environ.get("SITE_DOMAIN", "lanmarkfreight.net")
        self.canonical_host = os.environ.get("CANONICAL_HOST", f"hub.{self.site_domain}")
        self.company_name = os.environ.get("COMPANY_NAME", "Lanmark Freight")

        self.session_cookie = "hubsid"
        self.session_ttl_seconds = _int(os.environ.get("SESSION_TTL_SECONDS"), 12 * 60 * 60)

        # Budget for one reachability probe, and the size of the pool the probes are
        # handed to so a slow switch cannot occupy a request worker.
        self.probe_timeout_seconds = _int(os.environ.get("PROBE_TIMEOUT_SECONDS"), 12)
        self.probe_workers = _int(os.environ.get("PROBE_WORKERS"), 2)

        self.listen_port = _int(os.environ.get("PORT"), 8080)


settings = Settings()
