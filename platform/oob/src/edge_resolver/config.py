"""Runtime configuration, entirely from the environment.

Names follow the deployment's own vocabulary (``TELEMETRY_ENDPOINT``,
``SINKHOLE_ZONE``, ``RESOLVER_*``), which is what an internal edge service would
plausibly read. Port defaults are the privileged ones used inside the container; the
tests override them with 0, which asks the kernel for an ephemeral port.
"""

from __future__ import annotations

import ipaddress
import os
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from urllib.parse import urlsplit

DEFAULT_ZONE = "telemetry-edge.net"


def _int_env(env: dict[str, str], name: str, default: int) -> int:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _float_env(env: dict[str, str], name: str, default: float) -> float:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _csv(raw: str | None) -> tuple[str, ...]:
    return tuple(item.strip().lower() for item in (raw or "").split(",") if item.strip())


def _networks(raw: str | None) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    out = []
    for item in _csv(raw):
        try:
            out.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            continue  # a typo must not stop the service from listening
    return tuple(out)


def _source_map(raw: str | None) -> dict[str, str]:
    """``shopfront=10.5.0.4,10.5.0.5;billing=10.5.0.9`` -> {ip: app}."""
    mapping: dict[str, str] = {}
    for group in (raw or "").split(";"):
        name, sep, addresses = group.partition("=")
        if not sep:
            continue
        for address in _csv(addresses):
            mapping[address] = name.strip()
    return mapping


@dataclass(frozen=True)
class Config:
    """Immutable service configuration.

    ``public_ip`` is what the DNS listener puts in A answers. Left as None it is
    derived per client: we ask the kernel which local address it would use to reach
    the querying host, which is exactly the address that host can reach us back on.
    A fixed answer would hand out the wrong network half the time on a dual-homed host.

    ``admin_host`` defaults to ``"auto"``: the admin API must be reachable from the
    internal network only, so we bind it to the local address facing the reporting
    endpoint (which lives on that network and nowhere else). If that cannot be
    determined we fall back to loopback -- fail closed, never reachable from the
    application network.
    """

    zone: str = DEFAULT_ZONE
    telemetry_url: str = ""
    app: str = "edge-resolver"

    listen_host: str = "0.0.0.0"
    dns_udp_port: int = 53
    dns_tcp_port: int = 53
    http_port: int = 80
    https_port: int = 443
    smtp_port: int = 25
    ldap_port: int = 389

    admin_host: str = "auto"
    admin_port: int = 8901
    # The internal network carries the applications too, and an application is exactly
    # what a client takes control of first, so binding the admin API to the internal
    # interface is not by itself protection. Requests from outside these networks get
    # the same 404 as an unknown path.
    admin_networks: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = ()

    public_ip: str | None = None
    dns_ttl: int = 30
    store_size: int = 20000

    # We are the resolver for the application network, so we also have to answer the
    # names that network genuinely has: the reporting endpoint, databases, sibling
    # services. Those are forwarded to the upstream resolver inside this container
    # (Docker's embedded server, which can see both networks). Everything else is
    # answered with our own address.
    #
    # Two rules decide "genuinely internal": a single-label name (no dot at all), which
    # is what a compose service name looks like, and an explicit suffix list. Arbitrary
    # multi-label names are never forwarded -- that would be slow, would leak lookups
    # off the network, and would give a client a way to use us as an open resolver.
    internal_names: tuple[str, ...] = ()
    forward_single_label: bool = True
    upstream_timeout: float = 1.0
    # Names answered NXDOMAIN. Empty by default: a name that does not resolve is a
    # distinctive behaviour, so it exists only for the rare planted defect whose
    # condition is precisely that an outbound lookup fails.
    denylist: tuple[str, ...] = ()

    # Traffic from these source networks is the platform's own (seeding, self-test,
    # health probes). Identified by address rather than by a header, because a header
    # would be visible to the tool through any reflection or verbose error.
    synthetic_networks: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = ()
    # Static source-address -> app mapping, for deployments where no application ever
    # registers a hint. Learned mappings from hints take precedence.
    app_sources: dict[str, str] = field(default_factory=dict)

    known_tokens: frozenset[str] = frozenset()

    mail_name: str = ""
    ca_name: str = "Internal Issuing CA 1"
    default_cn: str = ""

    queue_size: int = 5000
    batch_size: int = 100
    flush_interval: float = 0.5
    request_timeout: float = 2.0

    # Attribution. The targeted lookup is the primary mechanism (one filtered request
    # per unattributed observation, off the listener path); the periodic full listing
    # only keeps the address-to-application map warm, so it can be slow and cheap.
    hint_poll_interval: float = 5.0
    hint_ttl: float = 120.0
    source_ttl: float = 900.0
    lookup_queue_size: int = 2000
    lookup_enabled: bool = True

    @property
    def owned_zone(self) -> str:
        """The owned zone, normalised for suffix comparisons."""
        return self.zone.strip(".").lower()

    @property
    def telemetry_host(self) -> str | None:
        if not self.telemetry_url:
            return None
        return urlsplit(self.telemetry_url).hostname

    @property
    def telemetry_port(self) -> int:
        return urlsplit(self.telemetry_url).port or 8900

    @property
    def mail_hostname(self) -> str:
        return self.mail_name or f"mx1.{self.owned_zone}"

    @property
    def default_certificate_name(self) -> str:
        return self.default_cn or f"edge1.{self.owned_zone}"

    def internal_suffixes(self) -> tuple[str, ...]:
        """Configured internal names plus the reporting endpoint's own hostname."""
        host = self.telemetry_host
        names = list(self.internal_names)
        if host and host.lower() not in names:
            names.append(host.lower())
        return tuple(names)

    def effective_admin_networks(
        self, extra: Iterable[str] = ()
    ) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
        """Who may read the admin API.

        Explicit configuration wins. Otherwise: loopback, plus the platform's own
        networks -- the same addresses whose traffic is treated as synthetic, which is
        precisely the definition of "us" -- plus any address the caller passes in (the
        reporting endpoint's, resolved at bind time). Applications live on the internal
        network too, so binding there is not by itself a boundary."""
        if self.admin_networks:
            return self.admin_networks
        networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = [
            ipaddress.ip_network("127.0.0.0/8"),
            ipaddress.ip_network("::1/128"),
        ]
        networks.extend(self.synthetic_networks)
        for address in extra:
            try:
                networks.append(ipaddress.ip_network(address, strict=False))
            except ValueError:
                continue
        return tuple(networks)

    def is_synthetic_source(self, address: str) -> bool:
        if not self.synthetic_networks:
            return False
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError:
            return False
        return any(parsed in network for network in self.synthetic_networks)

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Config":
        environ = dict(os.environ) if env is None else dict(env)
        return cls(
            zone=environ.get("SINKHOLE_ZONE", DEFAULT_ZONE),
            telemetry_url=environ.get("TELEMETRY_ENDPOINT", "").rstrip("/"),
            app=environ.get("RESOLVER_APP", "edge-resolver"),
            listen_host=environ.get("RESOLVER_LISTEN_HOST", "0.0.0.0"),
            dns_udp_port=_int_env(environ, "RESOLVER_DNS_PORT", 53),
            dns_tcp_port=_int_env(environ, "RESOLVER_DNS_PORT", 53),
            http_port=_int_env(environ, "RESOLVER_HTTP_PORT", 80),
            https_port=_int_env(environ, "RESOLVER_HTTPS_PORT", 443),
            smtp_port=_int_env(environ, "RESOLVER_SMTP_PORT", 25),
            ldap_port=_int_env(environ, "RESOLVER_LDAP_PORT", 389),
            admin_host=environ.get("RESOLVER_ADMIN_HOST", "auto"),
            admin_port=_int_env(environ, "RESOLVER_ADMIN_PORT", 8901),
            public_ip=environ.get("RESOLVER_PUBLIC_IP") or None,
            admin_networks=_networks(environ.get("RESOLVER_ADMIN_CIDRS")),
            dns_ttl=_int_env(environ, "RESOLVER_DNS_TTL", 30),
            store_size=_int_env(environ, "RESOLVER_STORE_SIZE", 20000),
            internal_names=_csv(environ.get("RESOLVER_INTERNAL_NAMES")),
            forward_single_label=environ.get("RESOLVER_FORWARD_SINGLE_LABEL", "1")
            not in ("0", "false", "no", "off"),
            upstream_timeout=_float_env(environ, "RESOLVER_UPSTREAM_TIMEOUT", 1.0),
            denylist=_csv(environ.get("RESOLVER_DENYLIST")),
            # The deployment already names the platform's own addresses once, for the
            # reporting endpoint; read that too rather than making it say it twice.
            synthetic_networks=_networks(
                environ.get("RESOLVER_SYNTHETIC_CIDRS")
                or environ.get("TELEMETRY_SYNTHETIC_CIDRS")
            ),
            app_sources=_source_map(environ.get("RESOLVER_APP_SOURCES")),
            known_tokens=frozenset(_csv(environ.get("RESOLVER_KNOWN_TOKENS"))),
            mail_name=environ.get("RESOLVER_MAIL_NAME", ""),
            ca_name=environ.get("RESOLVER_CA_NAME", "Internal Issuing CA 1"),
            default_cn=environ.get("RESOLVER_DEFAULT_CN", ""),
            hint_poll_interval=_float_env(environ, "RESOLVER_HINT_POLL", 5.0),
            lookup_enabled=environ.get("RESOLVER_LOOKUP", "1")
            not in ("0", "false", "no", "off"),
            hint_ttl=_float_env(environ, "RESOLVER_HINT_TTL", 120.0),
            source_ttl=_float_env(environ, "RESOLVER_SOURCE_TTL", 900.0),
        )

    def with_ports(self, **ports: int) -> "Config":
        return replace(self, **ports)
