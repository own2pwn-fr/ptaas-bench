"""Container liveness probe: ``python -m bench_oob.healthcheck``.

It queries the control API's /healthz rather than the DNS or HTTP listeners on purpose.
Probing a callback listener would record a callback on every tick -- polluting the store
and the run's event stream with the platform's own traffic -- whereas the control API is
inert by design. The bind address is derived exactly as the service derives it, so the
probe follows the service instead of assuming loopback.
"""

from __future__ import annotations

import sys
import urllib.request
from urllib.parse import urlsplit

from .config import Config
from .net import local_address_towards


def control_addresses(config: Config) -> list[str]:
    """Addresses to try, most likely first.

    Loopback is always tried as well: the service's own resolution and ours could
    disagree if DNS hiccups between startup and probe time, and a false "unhealthy"
    would restart a canary that is in fact serving."""
    if config.control_host != "auto":
        return [config.control_host]
    candidates: list[str] = []
    host = config.collector_host
    if host:
        port = urlsplit(config.collector_url).port or 8900
        derived = local_address_towards(host, port, default=None)
        if derived:
            candidates.append(derived)
    candidates.append("127.0.0.1")
    return candidates


def main() -> int:
    config = Config.from_env()
    errors: list[str] = []
    for address in control_addresses(config):
        url = f"http://{address}:{config.control_port}/healthz"
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                if response.status == 200:
                    return 0
                errors.append(f"{url}: HTTP {response.status}")
        except Exception as exc:  # noqa: BLE001 - a probe reports, it does not raise
            errors.append(f"{url}: {exc}")
    print("healthcheck failed: " + "; ".join(errors), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
