"""Environment parsing: the only configuration surface the container has."""

from __future__ import annotations

from bench_oob.config import Config


def test_defaults_are_the_container_ports():
    config = Config.from_env({})
    assert config.domain == "oob.bench.local"
    assert (config.dns_udp_port, config.http_port, config.https_port) == (53, 80, 443)
    assert (config.smtp_port, config.ldap_port, config.control_port) == (25, 389, 8901)
    assert config.collector_url == "" and config.app == "oob"


def test_compose_environment_is_understood():
    config = Config.from_env(
        {
            "BENCH_COLLECTOR_URL": "http://collector:8900/",
            "BENCH_OOB_DOMAIN": "oob.bench.local",
        }
    )
    assert config.collector_url == "http://collector:8900"  # trailing slash trimmed
    assert config.collector_host == "collector"


def test_zone_normalisation():
    assert Config(domain="OOB.Bench.Local.").zone == "oob.bench.local"


def test_known_tokens_are_parsed_and_lowercased():
    config = Config.from_env({"BENCH_OOB_KNOWN_TOKENS": "shop0031, SHOP0014 ,"})
    assert config.known_tokens == frozenset({"shop0031", "shop0014"})


def test_bad_integers_fall_back_to_the_default():
    """A typo in an env var must not stop the canary from listening."""
    assert Config.from_env({"BENCH_OOB_DNS_PORT": "not-a-port"}).dns_udp_port == 53
