"""Runtime configuration, entirely from the environment (12-factor, no config file).

Port defaults are the privileged ones used inside the container. Tests override
them with 0, which asks the kernel for an ephemeral port; the bound port is then
readable from ``OobService.ports``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from urllib.parse import urlsplit

DEFAULT_DOMAIN = "oob.bench.local"


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Config:
    """Immutable service configuration.

    ``public_ip`` is what the DNS listener puts in A answers. Left as ``None`` it is
    derived per client: we ask the kernel which local address it would use to reach
    the querying host, which is exactly the address that host can reach us back on.
    That keeps the dual-homed container honest without hardcoding a subnet.

    ``control_host`` defaults to ``"auto"``: the control API must be reachable from
    bench-internal only, so we bind it to the local address that faces the collector
    (which lives on bench-internal and nowhere else). If that cannot be determined we
    fall back to loopback -- fail closed, never expose the answer key to bench-public.
    """

    domain: str = DEFAULT_DOMAIN
    collector_url: str = ""
    app: str = "oob"

    listen_host: str = "0.0.0.0"
    dns_udp_port: int = 53
    dns_tcp_port: int = 53
    http_port: int = 80
    https_port: int = 443
    smtp_port: int = 25
    ldap_port: int = 389

    control_host: str = "auto"
    control_port: int = 8901

    public_ip: str | None = None
    dns_ttl: int = 5
    store_size: int = 5000
    known_tokens: frozenset[str] = field(default_factory=frozenset)

    # Collector client tuning; see bench_oob.collector.
    queue_size: int = 2000
    batch_size: int = 100
    flush_interval: float = 0.5
    collector_timeout: float = 2.0

    @property
    def zone(self) -> str:
        """The domain, normalised for suffix comparisons (lowercase, no trailing dot)."""
        return self.domain.strip(".").lower()

    @property
    def collector_host(self) -> str | None:
        if not self.collector_url:
            return None
        return urlsplit(self.collector_url).hostname

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Config":
        environ = os.environ if env is None else env
        known = environ.get("BENCH_OOB_KNOWN_TOKENS", "")
        cfg = cls(
            domain=environ.get("BENCH_OOB_DOMAIN", DEFAULT_DOMAIN),
            collector_url=environ.get("BENCH_COLLECTOR_URL", "").rstrip("/"),
            app=environ.get("BENCH_OOB_APP", "oob"),
            listen_host=environ.get("BENCH_OOB_LISTEN_HOST", "0.0.0.0"),
            dns_udp_port=_int_env("BENCH_OOB_DNS_PORT", 53),
            dns_tcp_port=_int_env("BENCH_OOB_DNS_PORT", 53),
            http_port=_int_env("BENCH_OOB_HTTP_PORT", 80),
            https_port=_int_env("BENCH_OOB_HTTPS_PORT", 443),
            smtp_port=_int_env("BENCH_OOB_SMTP_PORT", 25),
            ldap_port=_int_env("BENCH_OOB_LDAP_PORT", 389),
            control_host=environ.get("BENCH_OOB_CONTROL_HOST", "auto"),
            control_port=_int_env("BENCH_OOB_CONTROL_PORT", 8901),
            public_ip=environ.get("BENCH_OOB_PUBLIC_IP") or None,
            dns_ttl=_int_env("BENCH_OOB_DNS_TTL", 5),
            store_size=_int_env("BENCH_OOB_STORE_SIZE", 5000),
            known_tokens=frozenset(
                t.strip().lower() for t in known.split(",") if t.strip()
            ),
        )
        return cfg

    def with_ports(self, **ports: int) -> "Config":
        return replace(self, **ports)
