"""Environment parsing: the only configuration surface the container has."""

from __future__ import annotations

import ipaddress

from edge_resolver.config import Config


def test_defaults_are_the_container_ports():
    config = Config.from_env({})
    assert config.zone == "telemetry-edge.net"
    assert (config.dns_udp_port, config.http_port, config.https_port) == (53, 80, 443)
    assert (config.smtp_port, config.ldap_port, config.admin_port) == (25, 389, 8901)
    assert config.telemetry_url == "" and config.app == "edge-resolver"


def test_the_deployment_environment_is_understood():
    config = Config.from_env(
        {
            "TELEMETRY_ENDPOINT": "http://otel-collector:8900/",
            "SINKHOLE_ZONE": "telemetry-edge.net",
        }
    )
    assert config.telemetry_url == "http://otel-collector:8900"  # trailing slash trimmed
    assert config.telemetry_host == "otel-collector" and config.telemetry_port == 8900
    # The endpoint's own hostname is always internal, whatever else is configured:
    # resolving it to ourselves would swallow every application's telemetry.
    assert "otel-collector" in config.internal_suffixes()


def test_zone_normalisation():
    assert Config(zone="Telemetry-Edge.NET.").owned_zone == "telemetry-edge.net"


def test_platform_networks_are_read_from_either_name():
    """The deployment already names its own addresses once, for the reporting endpoint."""
    config = Config.from_env({"TELEMETRY_SYNTHETIC_CIDRS": "10.77.0.5/32"})
    assert config.is_synthetic_source("10.77.0.5")
    assert not config.is_synthetic_source("10.88.0.7")
    explicit = Config.from_env({"RESOLVER_SYNTHETIC_CIDRS": "10.88.0.0/24"})
    assert explicit.is_synthetic_source("10.88.0.7")


def test_admin_allowlist_defaults_to_the_platform_and_loopback():
    config = Config.from_env({"TELEMETRY_SYNTHETIC_CIDRS": "10.77.0.5/32"})
    networks = config.effective_admin_networks(["10.77.0.4/32"])
    assert ipaddress.ip_address("127.0.0.1") in networks[0]
    assert any(ipaddress.ip_address("10.77.0.5") in n for n in networks)
    assert any(ipaddress.ip_address("10.77.0.4") in n for n in networks)
    assert not any(ipaddress.ip_address("10.88.0.7") in n for n in networks)


def test_explicit_admin_cidrs_win():
    config = Config.from_env({"RESOLVER_ADMIN_CIDRS": "10.77.0.0/24"})
    networks = config.effective_admin_networks(["10.99.0.1/32"])
    assert networks == (ipaddress.ip_network("10.77.0.0/24"),)


def test_static_source_map_is_parsed():
    config = Config.from_env({"RESOLVER_APP_SOURCES": "shopfront=10.88.0.7,10.88.0.8;billing=10.88.0.9"})
    assert config.app_sources["10.88.0.8"] == "shopfront"
    assert config.app_sources["10.88.0.9"] == "billing"


def test_known_tokens_are_parsed_and_lowercased():
    config = Config.from_env({"RESOLVER_KNOWN_TOKENS": "shop0031, SHOP0014 ,"})
    assert config.known_tokens == frozenset({"shop0031", "shop0014"})


def test_bad_integers_fall_back_to_the_default():
    """A typo in an environment variable must not stop the service from listening."""
    assert Config.from_env({"RESOLVER_DNS_PORT": "not-a-port"}).dns_udp_port == 53
