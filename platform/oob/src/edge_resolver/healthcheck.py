"""Container liveness probe: ``python -m edge_resolver.healthcheck``.

It asks the admin API rather than any of the five listeners, on purpose. Probing a
listener would write an observation on every tick -- polluting the store and the event
stream with the platform's own traffic -- whereas the admin API records nothing. The
bind address is derived exactly as the service derives it, so the probe follows the
service instead of assuming loopback.
"""

from __future__ import annotations

import sys
import urllib.request

from .config import Config
from .net import local_address_towards


def admin_addresses(config: Config) -> list[str]:
    """Addresses to try, most likely first.

    Loopback is always tried too: the service's resolution and ours could disagree if
    name resolution hiccups between startup and probe time, and a false "unhealthy" would
    restart a process that is in fact serving."""
    if config.admin_host != "auto":
        return [config.admin_host]
    candidates: list[str] = []
    host = config.telemetry_host
    if host:
        derived = local_address_towards(host, config.telemetry_port, default=None)
        if derived:
            candidates.append(derived)
    candidates.append("127.0.0.1")
    return candidates


def main() -> int:
    config = Config.from_env()
    errors: list[str] = []
    for address in admin_addresses(config):
        url = f"http://{address}:{config.admin_port}/healthz"
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                if response.status == 200:
                    return 0
                errors.append(f"{url}: HTTP {response.status}")
        except Exception as exc:  # noqa: BLE001 - a probe reports, it does not raise
            errors.append(f"{url}: {exc}")
    print("probe failed: " + "; ".join(errors), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
