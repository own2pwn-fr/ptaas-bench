"""Preflight, and the real credentials file the `edge` target ships.

Every check here guards a failure that produces a plausible-looking empty result
rather than an error. That is the whole justification for stopping a run early: "the
harness was pointed at the wrong place" and "the tool missed everything" must never
be the same output.
"""

from __future__ import annotations

import pytest

from runners._lib.config import REPO_ROOT, AppSpec, BenchConfig, TargetCredentialsFile
from runners._lib.dockerctl import ExecResult
from runners._lib.preflight import (
    PreflightError,
    apps_with_authenticated_entrypoints,
    check_base_url_reachable,
    check_credentials,
    check_dns_from_tool_network,
    check_name_ambiguity,
    check_no_dev_services,
    check_no_published_ports,
    preflight,
)

from fakes import FakeDocker, FakeHttp

EDGE_CREDS = REPO_ROOT / "targets" / "edge" / "bench-credentials.yaml"


# -- the landed target -----------------------------------------------------------


@pytest.mark.skipif(not EDGE_CREDS.exists(), reason="the edge target has not landed")
def test_the_edge_credentials_file_parses_as_the_contract_specifies():
    target = TargetCredentialsFile.load(EDGE_CREDS)
    assert target.app == "edge"
    assert target.base_url == "http://www.halyardsupply.net"
    assert target.roles == ["user", "other-user"]
    assert not target.declares_no_login


@pytest.mark.skipif(not EDGE_CREDS.exists(), reason="the edge target has not landed")
def test_the_tool_facing_url_comes_from_the_target_not_from_apps_yaml():
    """A hostname derived from DEPLOY_SEED cannot be hardcoded on our side."""
    config = BenchConfig.load()
    fallbacks = config.resolve_urls()
    assert "edge" not in fallbacks
    assert config.apps["edge"].base_url == "http://www.halyardsupply.net"
    # ...while the internal name our own traffic uses stays harness-owned.
    assert config.apps["edge"].harness_url == "http://nginx"


@pytest.mark.skipif(not EDGE_CREDS.exists(), reason="the edge target has not landed")
def test_the_harness_logs_in_over_the_internal_name():
    """Our login must arrive from the platform's range or it is scored as the tool's."""
    config = BenchConfig.load()
    config.resolve_urls()
    creds = config.creds_for(config.apps["edge"], role="user")
    assert creds is not None
    assert creds.login_url == "http://nginx/account/login"
    assert creds.kind == "form"
    assert creds.username_field == "email"
    assert creds.logout_paths == ["/account/logout"]
    # The indicator is a literal in the contract, so it is escaped rather than
    # compiled as a pattern: a stray metacharacter must not change its meaning.
    assert creds.logged_in_regex == r"Welcome\ back"


@pytest.mark.skipif(not EDGE_CREDS.exists(), reason="the edge target has not landed")
def test_a_role_the_target_does_not_define_fails_loudly():
    config = BenchConfig.load()
    with pytest.raises(Exception, match="no user with role"):
        config.creds_for(config.apps["edge"], role="admin")


# -- credentials -----------------------------------------------------------------


def test_a_missing_credentials_file_stops_the_run(tmp_path):
    config = BenchConfig.load()
    app = AppSpec(key="nowhere", services=["x"], base_url="http://nowhere")
    checks = check_credentials(config, app, set())
    assert not checks[0].ok
    assert "does not exist" in checks[0].detail


def test_an_empty_users_list_is_fine_when_the_catalog_has_no_authenticated_entrypoints(tmp_path):
    """The contract asks a target with no login to say so explicitly."""
    path = tmp_path / "quiet" / "bench-credentials.yaml"
    path.parent.mkdir()
    path.write_text("app: quiet\nbase_url: http://quiet\nusers: []\n")
    config = BenchConfig.load()
    app = AppSpec(key="quiet", services=["x"], credentials_file=str(path))
    checks = {c.name: c for c in check_credentials(config, app, authed_apps=set())}
    assert checks["credentials-users:quiet"].ok


def test_an_empty_users_list_stops_the_run_when_the_catalog_expects_a_login(tmp_path):
    """Otherwise every authenticated flaw is reported as missed by every tool."""
    path = tmp_path / "quiet" / "bench-credentials.yaml"
    path.parent.mkdir()
    path.write_text("app: quiet\nbase_url: http://quiet\nusers: []\n")
    config = BenchConfig.load()
    app = AppSpec(key="quiet", services=["x"], credentials_file=str(path))
    checks = {c.name: c for c in check_credentials(config, app, authed_apps={"quiet"})}
    assert not checks["credentials-users:quiet"].ok
    assert "missed by every tool" in checks["credentials-users:quiet"].detail


def test_the_catalog_is_the_authority_on_which_apps_need_a_login():
    apps = apps_with_authenticated_entrypoints()
    # Read from the shipped catalog rather than assumed, so a target with genuinely
    # no authenticated surface is not required to invent credentials.
    assert isinstance(apps, set)


# -- the dev profile -------------------------------------------------------------


def test_a_running_dev_profile_service_stops_the_run():
    """It sits on a non-internal network: the tool would have a route out, and the
    out-of-band callbacks that make blind vulnerabilities measurable would leave
    instead of being captured."""
    docker = FakeDocker(compose_model={
        "services": {
            "edge-nginx": {"profiles": ["targets", "edge"]},
            "edge-devtap": {"profiles": ["dev"]},
        },
        "networks": {},
    })
    check = check_no_dev_services(docker)
    assert not check.ok
    assert "edge-devtap" in check.detail


def test_no_dev_services_running_passes():
    docker = FakeDocker(compose_model={
        "services": {"edge-nginx": {"profiles": ["targets"]}, "edge-devtap": {"profiles": ["dev"]}},
        "networks": {},
    })
    docker.compose_ps_id = lambda service: None if service == "edge-devtap" else f"cid-{service}"  # type: ignore[assignment]
    assert check_no_dev_services(docker).ok


def test_an_unreadable_compose_config_is_indeterminate_not_fatal():
    docker = FakeDocker(compose_model=None)
    check = check_no_dev_services(docker)
    assert check.indeterminate and not check.ok


# -- DNS from the tool's own network ---------------------------------------------

APP = AppSpec(key="edge", services=["edge-nginx"], base_url="http://www.halyardsupply.net")


def dns_docker(stdout: str, returncode: int = 0) -> FakeDocker:
    docker = FakeDocker()
    docker.run_capture = lambda *a, **k: ExecResult(["docker", "run"], returncode, stdout, "")  # type: ignore[assignment]
    return docker


def test_a_name_that_resolves_to_the_target_passes():
    docker = dns_docker("10.88.0.3  www.halyardsupply.net\n")
    check = check_dns_from_tool_network(
        docker, APP, "img", ["10.88.0.3"], network="bench-public", allow_pull=False
    )
    assert check.ok


def test_a_name_that_resolves_to_the_sinkhole_instead_of_the_target_fails():
    """The sinkhole answers every name on that network, so a wrong hostname does not
    fail to resolve -- the tool just spends its whole budget scanning the sinkhole."""
    docker = dns_docker("10.88.0.5  www.halyardsupply.net\n")
    check = check_dns_from_tool_network(
        docker, APP, "img", ["10.88.0.3"], network="bench-public", allow_pull=False
    )
    assert not check.ok
    assert "sinkhole" in check.detail


def test_a_name_that_does_not_resolve_fails():
    docker = dns_docker("", returncode=1)
    check = check_dns_from_tool_network(
        docker, APP, "img", ["10.88.0.3"], network="bench-public", allow_pull=False
    )
    assert not check.ok and not check.indeterminate


def test_an_image_with_no_resolver_tool_is_indeterminate():
    """An unverifiable condition must not block a run on its own."""
    docker = dns_docker("", returncode=42)
    check = check_dns_from_tool_network(
        docker, APP, "img", ["10.88.0.3"], network="bench-public", allow_pull=False
    )
    assert check.indeterminate and not check.ok


# -- the report ------------------------------------------------------------------


def test_preflight_raises_with_every_reason_at_once():
    config = BenchConfig.load()
    docker = FakeDocker(compose_model={
        "services": {"edge-devtap": {"profiles": ["dev"]}}, "networks": {},
    })
    report = preflight(
        config,
        [AppSpec(key="nowhere", services=["x"], base_url="http://nowhere")],
        docker,
        check_dns=False,
    )
    with pytest.raises(PreflightError) as excinfo:
        report.raise_if_failed()
    message = str(excinfo.value)
    assert "dev-profile" in message and "credentials:nowhere" in message
    assert "looks like a tool finding nothing" in message


def test_indeterminate_checks_do_not_stop_a_run():
    config = BenchConfig.load()
    docker = FakeDocker(compose_model=None)
    report = preflight(config, [], docker, check_dns=False)
    assert report.ok
    assert any(c.indeterminate for c in report.checks)


# -- the two bases ---------------------------------------------------------------


@pytest.mark.skipif(not EDGE_CREDS.exists(), reason="the edge target has not landed")
def test_a_driver_is_given_the_tool_facing_login_not_the_harness_one():
    """The internal alias does not exist on the sealed tool network.

    Handing a driver the harness's URL would make the tool fail to log in and scan
    anonymously, while still being reported as an authenticated scan.
    """
    from runners._lib.budget import Budget
    from runners._lib.config import ToolSpec
    from runners._lib.driver import RunContext

    config = BenchConfig.load()
    config.resolve_urls()
    app = config.apps["edge"]
    harness_creds = config.creds_for(app, role="user")
    ctx = RunContext(
        run_id="r",
        tool=ToolSpec(key="zap", image="i"),
        profile="full",
        apps=[app],
        budget=Budget(),
        run_dir=REPO_ROOT / "results" / "runs" / "_unused",
        docker=FakeDocker(),
        credentials={"edge": harness_creds},
    )
    assert harness_creds.login_url.startswith("http://nginx/")
    assert ctx.creds_for("edge").login_url == "http://www.halyardsupply.net/account/login"


def test_credentials_without_paths_are_not_rewritten():
    """A hand-written override with absolute URLs is left exactly as written."""
    from runners._lib.config import Credentials

    creds = Credentials(app="x", login_url="http://elsewhere/login")
    assert creds.for_base("http://somewhere-else").login_url == "http://elsewhere/login"


# -- published host ports ---------------------------------------------------------


def compose_model(services: dict, networks: dict | None = None) -> dict:
    return {"services": services, "networks": networks or {"bench-public": {"name": "bench-public"}}}


def test_a_published_port_on_the_tool_network_stops_the_run():
    """Publishing does not breach the egress seal, but it puts a target on a
    developer's loopback during a measured run and lets traffic reach it without
    passing the client accounting that decides how a request is scored."""
    docker = FakeDocker(compose_model=compose_model({
        "shopfront": {
            "networks": {"bench-public": {}, "bench-internal": {}},
            "ports": [{"mode": "ingress", "target": 3000, "published": "8280", "protocol": "tcp"}],
        },
    }))
    check = check_no_published_ports(docker, "bench-public")
    assert not check.ok
    assert "shopfront (8280)" in check.detail
    assert "client accounting" in check.detail


def test_the_check_ignores_the_profile_a_service_claims():
    """A port published outside any profile is up whenever the target is."""
    docker = FakeDocker(compose_model=compose_model({
        "sneaky": {
            "profiles": ["dev"],
            "networks": {"bench-public": {}},
            "ports": ["9000:80"],
        },
    }))
    assert not check_no_published_ports(docker, "bench-public").ok


def test_a_published_port_on_another_network_is_not_our_business():
    """The dev tap sits on its own non-internal network by design."""
    docker = FakeDocker(compose_model=compose_model(
        {"edge-devtap": {"networks": {"edge-dev": {}}, "ports": ["8180:80"]}},
        networks={"bench-public": {"name": "bench-public"}, "edge-dev": {"name": "edge-dev"}},
    ))
    assert check_no_published_ports(docker, "bench-public").ok


def test_the_network_name_is_resolved_not_assumed():
    """A fragment may key the network differently from its real docker name."""
    docker = FakeDocker(compose_model=compose_model(
        {"web": {"networks": {"front": {}}, "ports": ["80:80"]}},
        networks={"front": {"name": "bench-public"}},
    ))
    assert not check_no_published_ports(docker, "bench-public").ok


def test_no_published_ports_passes():
    docker = FakeDocker(compose_model=compose_model(
        {"shopfront": {"networks": {"bench-public": {}, "bench-internal": {}}}}
    ))
    assert check_no_published_ports(docker, "bench-public").ok


# -- name ambiguity ---------------------------------------------------------------


def test_a_name_on_both_networks_is_reported():
    """Interface selection is then a coin toss for anything connecting by name."""
    app = AppSpec(
        key="shopfront",
        services=["shopfront"],
        base_url="http://shopfront:3000",
        internal_url="http://shopfront:3000",
    )
    docker = FakeDocker(compose_model=compose_model(
        {"shopfront": {"networks": {
            "bench-public": {"aliases": ["shopfront", "storefront-web-01"]},
            "bench-internal": {"aliases": ["shopfront"]},
        }}},
        networks={
            "bench-public": {"name": "bench-public"},
            "bench-internal": {"name": "bench-internal"},
        },
    ))
    check = check_name_ambiguity(docker, app, ("bench-public", "bench-internal"))
    # Reported, not fatal: it is how the target chose to name itself, and the harness
    # pins its own connections to the internal address instead of resolving.
    assert check.ok
    assert "'shopfront'" in check.detail
    # Only the names actually in use are reported; the bare service name resolves on
    # every network a service joins, so listing it would be noise on every target.
    assert "storefront-web-01" not in check.detail


def test_distinct_names_per_network_are_unambiguous():
    app = AppSpec(
        key="edge",
        services=["edge-nginx"],
        base_url="http://www.example.test",
        internal_url="http://nginx",
    )
    docker = FakeDocker(compose_model=compose_model(
        {"edge-nginx": {"networks": {
            "bench-public": {"aliases": ["www.example.test"]},
            "bench-internal": {"aliases": ["nginx"]},
        }}},
        networks={
            "bench-public": {"name": "bench-public"},
            "bench-internal": {"name": "bench-internal"},
        },
    ))
    check = check_name_ambiguity(docker, app, ("bench-public", "bench-internal"))
    assert check.ok and "one network each" in check.detail


# -- against the real compose files ------------------------------------------------


@pytest.mark.skipif(
    not (REPO_ROOT / "compose" / "shopfront.yml").exists(), reason="no target fragments"
)
def test_the_shipped_fragments_publish_nothing_on_the_tool_network():
    """Read from the fragments as committed, so a target that adds a port is caught
    here rather than during someone's benchmark run."""
    import yaml

    services: dict = {}
    networks = {
        "bench-public": {"name": "bench-public"},
        "bench-internal": {"name": "bench-internal"},
    }
    for fragment in sorted((REPO_ROOT / "compose").glob("*.yml")):
        doc = yaml.safe_load(fragment.read_text()) or {}
        services.update(doc.get("services") or {})
        for key, spec in (doc.get("networks") or {}).items():
            networks.setdefault(key, spec or {"name": key})
    docker = FakeDocker(compose_model={"services": services, "networks": networks})
    check = check_no_published_ports(docker, "bench-public")
    assert check.ok, check.detail


# -- reachability, which is not the same as resolution -----------------------------


def test_a_base_url_that_refuses_connections_stops_the_run():
    """The intranet case: the name resolves through the sinkhole whatever happens,
    and a base URL naming the wrong port then refuses every connection. The run is a
    findings file with nothing in it, which reads as a scanner with no coverage."""
    from runners._lib.internal_http import Response

    app = AppSpec(key="intranet", services=["intranet-hub"], base_url="http://hub.example")
    http = FakeHttp(default=Response(0, "", error="ConnectionRefusedError: [Errno 111]"))
    check = check_base_url_reachable(http, app, "10.88.0.7")
    assert not check.ok
    assert "refuses connections" in check.detail
    assert "Errno 111" in check.detail


def test_any_http_answer_counts_as_reachable():
    """The question is whether something is listening, not whether it likes us."""
    from runners._lib.internal_http import Response

    app = AppSpec(key="x", services=["x"], base_url="http://x.example")
    for status in (200, 401, 404, 500):
        check = check_base_url_reachable(FakeHttp(default=Response(status, "")), app, None)
        assert check.ok, status


def test_the_probe_is_aimed_at_the_targets_address_on_the_tool_network():
    """Probing by name would resolve to whichever interface, and the point is to
    check the one the tool will use."""
    from runners._lib.internal_http import Response

    app = AppSpec(key="x", services=["x"], base_url="http://x.example")
    http = FakeHttp(default=Response(200, ""))
    check_base_url_reachable(http, app, "10.88.0.9")
    assert http.connected_to == ["10.88.0.9"]
    assert http.requests == [("GET", "http://x.example/")]


def test_a_target_with_no_base_url_at_all_fails():
    app = AppSpec(key="x", services=["x"])
    assert not check_base_url_reachable(FakeHttp(), app, None).ok


# -- the catalog and the registry must agree ---------------------------------------


def test_every_app_in_the_catalog_has_an_entry_in_apps_yaml():
    """An app the catalog plants but the harness does not know about is never
    scanned, and its entries are counted as missed by every tool."""
    import yaml

    catalog_apps = set()
    for path in (REPO_ROOT / "catalog" / "vulns").glob("*.yaml"):
        doc = yaml.safe_load(path.read_text()) or {}
        if doc.get("app"):
            catalog_apps.add(str(doc["app"]))
    registry = set(BenchConfig.load().apps)
    assert not (catalog_apps - registry), f"planted but unreachable: {catalog_apps - registry}"
    assert not (registry - catalog_apps), f"in apps.yaml with no ground truth: {registry - catalog_apps}"
