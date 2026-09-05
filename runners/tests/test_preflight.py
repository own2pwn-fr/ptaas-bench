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
    check_credentials,
    check_dns_from_tool_network,
    check_no_dev_services,
    preflight,
)

from fakes import FakeDocker

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
    docker = FakeDocker()
    docker.compose_services_by_profile = lambda: {  # type: ignore[assignment]
        "edge-nginx": ["targets", "edge"],
        "edge-devtap": ["dev"],
    }
    check = check_no_dev_services(docker)
    assert not check.ok
    assert "edge-devtap" in check.detail


def test_no_dev_services_running_passes():
    docker = FakeDocker()
    docker.compose_services_by_profile = lambda: {"edge-nginx": ["targets"], "edge-devtap": ["dev"]}  # type: ignore[assignment]
    docker.compose_ps_id = lambda service: None if service == "edge-devtap" else f"cid-{service}"  # type: ignore[assignment]
    assert check_no_dev_services(docker).ok


def test_an_unreadable_compose_config_is_indeterminate_not_fatal():
    docker = FakeDocker()
    docker.compose_services_by_profile = lambda: None  # type: ignore[assignment]
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
    docker = FakeDocker()
    docker.compose_services_by_profile = lambda: {"edge-devtap": ["dev"]}  # type: ignore[assignment]
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
    docker = FakeDocker()
    docker.compose_services_by_profile = lambda: None  # type: ignore[assignment]
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
