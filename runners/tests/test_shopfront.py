"""The `shopfront` target, and the process state a digest cannot see.

shopfront plants two prototype-pollution flaws. Their effect lives in the Node
process, not in PostgreSQL, so `state-reset` restores the database, the uploads and
the key directory and prints an identical digest while `Object.prototype` is still
polluted. The next tool then inherits the previous tool's exploit and is credited
with it -- the most direct route there is from one scanner's work to another's score,
and precisely what the reset verification exists to prevent.

Hence the inverted default: every service is restarted unless a target opts out.
"""

from __future__ import annotations

import pytest

from runners._lib.config import REPO_ROOT, AppSpec, BenchConfig, TargetCredentialsFile
from runners._lib.dockerctl import ExecResult
from runners._lib.preflight import check_live_credentials, emit_credentials, js_dependent_entries
from runners._lib.reset import TargetResetter

from fakes import FakeClock, FakeDocker, reset_ok

SHOPFRONT_CREDS = REPO_ROOT / "targets" / "shopfront" / "bench-credentials.yaml"
landed = pytest.mark.skipif(
    not SHOPFRONT_CREDS.exists(), reason="the shopfront target has not landed"
)


# -- restart is the default -------------------------------------------------------


def test_every_service_is_restarted_unless_the_target_opts_out():
    app = AppSpec(key="shopfront", services=["shopfront", "shopfront-db"], base_url="http://x")
    assert app.services_to_restart == ["shopfront", "shopfront-db"]
    assert not app.restart_opted_out


def test_a_target_can_opt_out_explicitly():
    app = AppSpec(key="x", services=["a", "b"], base_url="http://x", restart_services=[])
    assert app.services_to_restart == []
    assert app.restart_opted_out


def test_the_reset_restarts_before_running_the_command():
    """A digest over persistent storage is blind to a polluted prototype."""
    app = AppSpec(
        key="shopfront",
        services=["shopfront", "shopfront-db"],
        base_url="http://shopfront:3000",
        reset_service="shopfront",
    )
    docker = FakeDocker(
        started_at={"cid-shopfront": "2026-09-05T17:00:00.000000000Z",
                    "cid-shopfront-db": "2026-09-05T17:00:00.000000000Z"},
        exec_results={"shopfront": reset_ok("state 9f1c0b7d4e2a customers=42 products=118")},
    )
    clock = FakeClock()
    outcome = TargetResetter(docker, sleep=clock.sleep, clock=clock).reset(app)
    assert outcome.ok, outcome.failures
    assert ("compose_restart", ["shopfront", "shopfront-db"]) in docker.calls
    # ...and the restart is checked, not merely requested.
    assert {c.name for c in outcome.checks} >= {"restarted:shopfront", "restarted:shopfront-db"}


def test_the_multi_token_digest_line_survives():
    """shopfront prints `state <digest> customers=… products=… orders=… tickets=…`."""
    docker = FakeDocker(
        exec_results={
            "shopfront": reset_ok(
                "state 4b1d9c77aa20 customers=42 products=118 orders=63 tickets=9"
            )
        }
    )
    app = AppSpec(key="shopfront", services=["shopfront"], base_url="http://x",
                  reset_service="shopfront", restart_services=[])
    clock = FakeClock()
    outcome = TargetResetter(docker, sleep=clock.sleep, clock=clock).reset(app)
    assert outcome.state_digest == (
        "state 4b1d9c77aa20 customers=42 products=118 orders=63 tickets=9"
    )
    # The counts are part of the compared value on purpose: a reset that restores the
    # digest but not the row counts has not restored the estate.
    assert outcome.ok


# -- the shipped configuration -----------------------------------------------------


@landed
def test_shopfront_is_configured_to_restart_both_services():
    config = BenchConfig.load()
    config.resolve_urls()
    app = config.apps["shopfront"]
    assert app.services_to_restart == ["shopfront", "shopfront-db"]
    assert app.reset_service == "shopfront"


@landed
def test_shopfront_urls_come_from_the_target():
    config = BenchConfig.load()
    fallbacks = config.resolve_urls()
    assert "shopfront" not in fallbacks
    assert config.apps["shopfront"].base_url == "http://shopfront:3000"


@landed
def test_shopfront_declares_the_three_roles_the_catalog_needs():
    """Two entries can only be proved from an account that does not own the resource."""
    target = TargetCredentialsFile.load(SHOPFRONT_CREDS)
    assert set(target.roles) == {"user", "other-user", "admin"}


@landed
def test_the_session_probe_indicators_are_matched_as_literals():
    config = BenchConfig.load()
    config.resolve_urls()
    creds = config.creds_for(config.apps["shopfront"], role="other-user")
    assert creds.kind == "json"
    assert creds.username_field == "email"
    assert creds.logged_in_regex == "authenticated"
    assert "/api/auth/logout" in creds.logout_paths


# -- live credentials --------------------------------------------------------------


def test_the_target_is_asked_for_its_current_identities():
    """The committed file belongs to one DEPLOY_SEED; the running target is the truth."""
    emitted = (
        "app: shopfront\n"
        "users:\n"
        "  - role: user\n"
        "    username: rebuilt@example.test\n"
        "    password: pw\n"
        "    subject_id: '1001'\n"
    )
    docker = FakeDocker(exec_results={"shopfront": ExecResult(["exec"], 0, emitted, "")})
    app = AppSpec(key="shopfront", services=["shopfront"], base_url="http://x",
                  reset_service="shopfront")
    doc, detail = emit_credentials(docker, app)
    assert detail == "ok"
    assert doc["users"][0]["username"] == "rebuilt@example.test"
    # It is the reset command with a flag, not a second interface to maintain.
    assert ("compose_exec", ("shopfront", ["/usr/local/bin/state-reset", "--emit-credentials"])) in docker.calls


EMITTED_OTHER_SEED = (
    "app: shopfront\n"
    "users:\n"
    "  - role: user\n"
    "    username: seed2-user@example.test\n"
    "    password: other\n"
    "    subject_id: '1001'\n"
)


@landed
def test_a_committed_file_from_another_seed_stops_the_run():
    """The harness could scan happily with the live identities, which is the problem.

    Everything else that reads the file -- the target's selftest, the scorer's view
    of which subject owns what -- would then describe a different deployment from the
    one being scanned, and the published result would rest on credentials nobody can
    reproduce from the repository.
    """
    config = BenchConfig.load()
    config.resolve_urls()
    app = config.apps["shopfront"]
    docker = FakeDocker(exec_results={"shopfront": ExecResult(["exec"], 0, EMITTED_OTHER_SEED, "")})
    check = check_live_credentials(config, app, docker, fatal_on_mismatch=True)
    assert not check.ok
    assert "different\nDEPLOY_SEED" in check.detail or "different DEPLOY_SEED" in check.detail
    # The operator is told exactly how to fix it.
    assert "--emit-credentials >" in check.detail


@landed
def test_the_live_identities_are_used_when_the_mismatch_is_waived():
    """--allow-stale-credentials must produce a correct scan, not an anonymous one."""
    config = BenchConfig.load()
    config.resolve_urls()
    app = config.apps["shopfront"]
    docker = FakeDocker(exec_results={"shopfront": ExecResult(["exec"], 0, EMITTED_OTHER_SEED, "")})
    check = check_live_credentials(config, app, docker, fatal_on_mismatch=False)
    assert check.ok
    creds = config.creds_for(app, role="user")
    assert creds.username == "seed2-user@example.test"
    # The login shape comes from the committed file and is not seed-dependent.
    assert creds.login_url.endswith("/api/auth/login")


@landed
def test_a_target_without_the_flag_falls_back_to_the_file_and_says_so():
    config = BenchConfig.load()
    config.resolve_urls()
    app = config.apps["shopfront"]
    docker = FakeDocker(
        exec_results={"shopfront": ExecResult(["exec"], 2, "", "unknown option")}
    )
    check = check_live_credentials(config, app, docker)
    assert check.indeterminate
    assert "belongs to one DEPLOY_SEED" in check.detail


# -- browsers ----------------------------------------------------------------------


def test_the_catalog_says_which_entries_need_javascript():
    apps = [AppSpec(key="shopfront", services=["x"], base_url="http://x")]
    counts = js_dependent_entries(apps)
    assert set(counts) == {"shopfront"}
    assert counts["shopfront"] >= 0


def test_drivers_declare_whether_they_drive_a_browser(tmp_path):
    from test_drivers import SHOP, make_ctx  # noqa: PLC0415 - shared driver fixtures

    from runners.nikto import NiktoDriver
    from runners.nuclei import NucleiDriver
    from runners.zap import ZapDriver

    zap = ZapDriver()
    ctx = make_ctx(tmp_path, "zap", "baseline", [SHOP])
    assert zap.drives_a_browser(ctx, zap.plan(ctx))[0] is True

    nuclei = NucleiDriver()
    ctx = make_ctx(tmp_path, "nuclei", "default", [SHOP])
    declared, reason = nuclei.drives_a_browser(ctx, nuclei.plan(ctx))
    # The image bundles chromium, but the driver does not enable headless templates:
    # the record states what ran, not what could have.
    assert declared is False and "not passed" in reason

    nikto = NiktoDriver()
    ctx = make_ctx(tmp_path, "nikto", "default", [SHOP])
    assert nikto.drives_a_browser(ctx, nikto.plan(ctx))[0] is False


# -- route templates in the exclusion list -----------------------------------------


def test_a_route_template_becomes_a_valid_regex_for_zap():
    """`{id}` is an invalid repetition in Java, which is what ZAP compiles with: a
    context carrying it verbatim is rejected and the whole plan fails to load."""
    import re

    from runners._lib.config import Credentials

    creds = Credentials(app="x", logout_paths=["/api/account/sessions/{id}", "/logout"])
    patterns = creds.logout_regexes()
    assert patterns == [".*/api/account/sessions/[^/]+.*", ".*/logout.*"]
    for pattern in patterns:
        re.compile(pattern)
    assert re.match(patterns[0], "http://x/api/account/sessions/7f2c")


def test_a_route_template_becomes_a_prefix_for_substring_matchers():
    """wapiti's --exclude takes a URL and skipfish's -X a substring: a literal `{id}`
    matches nothing, which reads as a working exclusion until the scanner deletes its
    own session and finishes the scan anonymously."""
    from runners._lib.config import Credentials

    creds = Credentials(app="x", logout_paths=["/api/account/sessions/{id}", "/logout"])
    assert creds.logout_prefixes() == ["/api/account/sessions", "/logout"]


@landed
def test_no_driver_emits_an_unexpandable_template(tmp_path):
    import re
    import shlex

    import yaml
    from test_drivers import make_ctx  # noqa: PLC0415

    from runners.skipfish import SkipfishDriver
    from runners.wapiti import WapitiDriver
    from runners.zap import ZapDriver

    config = BenchConfig.load()
    config.resolve_urls()
    app = config.apps["shopfront"]
    creds = {"shopfront": config.creds_for(app, role="user")}

    ctx = make_ctx(tmp_path, "zap", "full", [app], creds=creds)
    ZapDriver().plan(ctx)
    plan = yaml.safe_load((ctx.conf_dir / "zap-shopfront.yaml").read_text())
    for pattern in plan["env"]["contexts"][0]["excludePaths"]:
        re.compile(pattern)  # would raise on a stray `{id}`
        assert "{" not in pattern

    args = WapitiDriver().plan(make_ctx(tmp_path, "wapiti", "default", [app], creds=creds))[0].args
    assert not any("{" in a for a in args)

    script = SkipfishDriver().plan(make_ctx(tmp_path, "skipfish", "default", [app], creds=creds))[0].args[1]
    assert not any("{" in t for t in shlex.split(script.split("exec ", 1)[1]))
