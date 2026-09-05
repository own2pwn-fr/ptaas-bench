"""Each driver's translation of the neutral configuration into its tool's dialect.

These tests are the reason the harness can claim its authenticated scans are
comparable: they assert that every tool is given the same credentials, the same
exclusions and the same budget, expressed in whatever each one understands.
"""

from __future__ import annotations

import shlex

import pytest
import yaml

from runners._lib.budget import Budget
from runners._lib.config import AppSpec, Credentials, ToolSpec
from runners._lib.driver import RunContext
from runners._lib.login import Session
from runners.generic import SENTINEL, GenericDriver
from runners.nikto import NiktoDriver
from runners.nuclei import NucleiDriver
from runners.skipfish import SkipfishDriver
from runners.wapiti import WapitiDriver
from runners.zap import ZapDriver

from fakes import FakeClock, FakeDocker

SHOP = AppSpec(
    key="shopfront",
    services=["shopfront-web"],
    base_url="http://shopfront-web:3000",
    openapi_url="http://shopfront-web:3000/api/openapi.json",
    graphql_url="http://shopfront-web:3000/graphql",
)
LEGACY = AppSpec(key="legacy", services=["legacy-web"], base_url="http://legacy-web")

JSON_CREDS = Credentials(
    app="shopfront",
    kind="json",
    login_url="http://shopfront-web:3000/api/auth/login",
    login_page_url="http://shopfront-web:3000/login",
    username="customer@bench.local",
    password="hunter2",
    username_field="email",
    verify_url="http://shopfront-web:3000/api/me",
    logged_in_regex='"customer_id"',
    token_json_path="data.access_token",
    session="bearer",
    logout_paths=["/api/auth/logout"],
)
FORM_CREDS = Credentials(
    app="legacy",
    kind="form",
    login_url="http://legacy-web/login.php",
    login_page_url="http://legacy-web/login.php",
    username="jdoe",
    password="s3cr3t",
    verify_url="http://legacy-web/account.php",
    logged_in_regex="Sign out",
    logout_paths=["/logout.php"],
)
SESSION = Session(
    app="shopfront",
    cookies={"sid": "abc123"},
    headers={"Authorization": "Bearer eyJ0"},
    verified=True,
)


def make_ctx(tmp_path, tool_key, profile, apps, creds=None, sessions=None, budget=None):
    clock = FakeClock()
    ctx = RunContext(
        run_id="run-abcdef012345",
        tool=ToolSpec(key=tool_key, image=f"example/{tool_key}:1"),
        profile=profile,
        apps=apps,
        budget=budget or Budget(wall_clock_s=3600, poll_interval_s=5),
        run_dir=tmp_path,
        docker=FakeDocker(),
        credentials=creds or {},
        sessions=sessions or {},
        # The fake clock only advances when the code under test sleeps, so a wait
        # loop that forgets to consult its budget hangs the test instead of quietly
        # passing after a real-time delay.
        clock=clock,
        sleep=clock.sleep,
    )
    ctx.ensure_dirs()
    return ctx


# -- ZAP -------------------------------------------------------------------------


def zap_plan(tmp_path, profile="full", creds=None):
    ctx = make_ctx(tmp_path, "zap", profile, [SHOP], creds=creds)
    invocations = ZapDriver().plan(ctx)
    plan = yaml.safe_load((ctx.conf_dir / "zap-shopfront.yaml").read_text())
    return invocations[0], plan


def test_zap_runs_the_automation_framework_plan(tmp_path):
    inv, plan = zap_plan(tmp_path)
    assert inv.args == ["zap.sh", "-cmd", "-silent", "-autorun", "/bench/conf/zap-shopfront.yaml"]
    assert set(plan) == {"env", "jobs"}


def test_zap_never_updates_addons_at_run_time(tmp_path):
    """The recorded image digest must fully determine what the tool did."""
    inv, _ = zap_plan(tmp_path)
    assert "-addonupdate" not in inv.args


def test_zap_full_profile_active_scans_and_baseline_does_not(tmp_path):
    _, full = zap_plan(tmp_path, "full")
    _, baseline = zap_plan(tmp_path, "baseline")
    assert "activeScan" in [j["type"] for j in full["jobs"]]
    assert "activeScan" not in [j["type"] for j in baseline["jobs"]]
    # Both crawl, including with the AJAX spider: the corpus is SPA-heavy and
    # "runOnlyIfModern" would skip exactly the targets the headline result is about.
    for plan in (full, baseline):
        types = [j["type"] for j in plan["jobs"]]
        assert "spider" in types and "spiderAjax" in types
    ajax = next(j for j in full["jobs"] if j["type"] == "spiderAjax")
    assert ajax["parameters"]["runOnlyIfModern"] is False


def test_zap_imports_an_api_description_when_the_target_offers_one(tmp_path):
    _, plan = zap_plan(tmp_path)
    jobs = {j["type"]: j for j in plan["jobs"]}
    assert jobs["openapi"]["parameters"]["apiUrl"] == "http://shopfront-web:3000/api/openapi.json"
    assert jobs["graphql"]["parameters"]["endpoint"] == "http://shopfront-web:3000/graphql"
    # The graphql job takes neither context nor user; inventing them breaks the plan.
    assert set(jobs["graphql"]["parameters"]) == {"endpoint", "queryGenEnabled", "requestMethod"}


def test_zap_reports_traditional_json_into_the_mounted_raw_directory(tmp_path):
    inv, plan = zap_plan(tmp_path)
    report = next(j for j in plan["jobs"] if j["type"] == "report")
    assert report["parameters"]["template"] == "traditional-json"
    assert report["parameters"]["reportDir"] == "/bench/raw"
    assert report["parameters"]["reportFile"] == "zap-shopfront.json"
    assert inv.artifacts == ["zap-shopfront.json"]
    # Nothing is filtered out: the scoring engine decides what counts, and a driver
    # that drops informational alerts is a driver hiding false positives.
    assert set(report["risks"]) == {"high", "medium", "low", "info"}


def test_zap_passive_alerts_are_uncapped(tmp_path):
    """The packaged scans cap this at 10 per rule, truncating what the scorer counts."""
    _, plan = zap_plan(tmp_path)
    passive = next(j for j in plan["jobs"] if j["type"] == "passiveScan-config")
    assert passive["parameters"]["maxAlertsPerRule"] == 0


def test_zap_credentials_never_land_in_the_plan_file(tmp_path):
    """The plan ships with the results; the password must not."""
    inv, plan = zap_plan(tmp_path, creds={"shopfront": JSON_CREDS})
    text = yaml.safe_dump(plan)
    assert "hunter2" not in text
    assert plan["env"]["contexts"][0]["users"][0]["credentials"] == {
        "username": "${ZAP_USER}", "password": "${ZAP_PASS}"
    }
    assert inv.env == {"ZAP_USER": "customer@bench.local", "ZAP_PASS": "hunter2"}


def test_zap_json_login_becomes_a_json_auth_context(tmp_path):
    _, plan = zap_plan(tmp_path, creds={"shopfront": JSON_CREDS})
    context = plan["env"]["contexts"][0]
    assert context["authentication"]["method"] == "json"
    body = context["authentication"]["parameters"]["loginRequestBody"]
    assert '"email": "{%username%}"' in body and '"password": "{%password%}"' in body
    # A bearer session is replayed through ZAP's header session management, using its
    # own {%json:...%} extraction rather than a token we captured and froze.
    assert context["sessionManagement"] == {
        "method": "headers",
        "parameters": {"Authorization": "Bearer {%json:data.access_token%}"},
    }
    assert context["authentication"]["verification"]["method"] == "poll"


def test_zap_form_login_keeps_the_placeholders_unencoded(tmp_path):
    ctx = make_ctx(tmp_path, "zap", "full", [LEGACY], creds={"legacy": FORM_CREDS})
    ZapDriver().plan(ctx)
    plan = yaml.safe_load((ctx.conf_dir / "zap-legacy.yaml").read_text())
    params = plan["env"]["contexts"][0]["authentication"]["parameters"]
    assert params["loginRequestBody"] == "username={%username%}&password={%password%}"


def test_zap_excludes_the_control_plane_and_the_logout(tmp_path):
    """A scanner that reaches /__bench__ can reset the target it is being scored on."""
    _, plan = zap_plan(tmp_path, creds={"shopfront": JSON_CREDS})
    excludes = plan["env"]["contexts"][0]["excludePaths"]
    assert any("__bench__" in e for e in excludes)
    assert any("logout" in e for e in excludes)
    spider = next(j for j in plan["jobs"] if j["type"] == "spider")
    assert spider["parameters"]["logoutAvoidance"] is True


def test_zap_job_durations_follow_the_budget(tmp_path):
    ctx = make_ctx(tmp_path, "zap", "full", [SHOP], budget=Budget(wall_clock_s=1200))
    ZapDriver().plan(ctx)
    plan = yaml.safe_load((ctx.conf_dir / "zap-shopfront.yaml").read_text())
    active = next(j for j in plan["jobs"] if j["type"] == "activeScan")
    # 20 minutes total, 90% usable, half of that to the active scan -> 9.
    assert active["parameters"]["maxScanDurationInMins"] == 9


# -- nuclei ----------------------------------------------------------------------


def test_nuclei_writes_jsonl_and_never_self_updates(tmp_path):
    ctx = make_ctx(tmp_path, "nuclei", "default", [SHOP])
    args = NucleiDriver().plan(ctx)[0].args
    assert "-jsonl" in args
    assert args[args.index("-output") + 1] == "/bench/raw/nuclei-shopfront.jsonl"
    assert "-disable-update-check" in args


def test_nuclei_dast_profile_enables_fuzzing_templates(tmp_path):
    default = NucleiDriver().plan(make_ctx(tmp_path, "nuclei", "default", [SHOP]))[0].args
    dast = NucleiDriver().plan(make_ctx(tmp_path, "nuclei", "dast", [SHOP]))[0].args
    assert "-dast" not in default
    assert "-dast" in dast


def test_nuclei_gets_the_session_as_headers(tmp_path):
    """nuclei cannot log in; -header is the whole of its auth support."""
    ctx = make_ctx(tmp_path, "nuclei", "default", [SHOP], sessions={"shopfront": SESSION})
    args = NucleiDriver().plan(ctx)[0].args
    headers = [args[i + 1] for i, a in enumerate(args) if a == "-header"]
    assert "Authorization: Bearer eyJ0" in headers
    assert "Cookie: sid=abc123" in headers


def test_nuclei_offline_profile_disables_interactsh(tmp_path):
    args = NucleiDriver().plan(make_ctx(tmp_path, "nuclei", "offline", [SHOP]))[0].args
    assert "-no-interactsh" in args


# -- wapiti ----------------------------------------------------------------------


def test_wapiti_always_starts_from_a_cold_session(tmp_path):
    """wapiti resumes its sqlite session by default and silently skips work."""
    args = WapitiDriver().plan(make_ctx(tmp_path, "wapiti", "default", [SHOP]))[0].args
    assert "--flush-session" in args and "--flush-attacks" in args


def test_wapiti_time_cap_stays_under_its_share(tmp_path):
    """wapiti has a real global cap, so it should finish and write its own report."""
    ctx = make_ctx(tmp_path, "wapiti", "default", [SHOP], budget=Budget(wall_clock_s=1200))
    args = WapitiDriver().plan(ctx)[0].args
    assert int(args[args.index("--max-scan-time") + 1]) == 1020


def test_wapiti_uses_its_native_form_login_when_it_can(tmp_path):
    ctx = make_ctx(tmp_path, "wapiti", "default", [LEGACY], creds={"legacy": FORM_CREDS})
    args = WapitiDriver().plan(ctx)[0].args
    assert args[args.index("--form-user") + 1] == "jdoe"
    assert args[args.index("--form-url") + 1] == "http://legacy-web/login.php"


def test_wapiti_falls_back_to_the_harness_session_for_json_logins(tmp_path):
    ctx = make_ctx(
        tmp_path, "wapiti", "default", [SHOP],
        creds={"shopfront": JSON_CREDS}, sessions={"shopfront": SESSION},
    )
    args = WapitiDriver().plan(ctx)[0].args
    # -C (capital) is a literal cookie string; -c would be a cookie *file*.
    assert args[args.index("--cookie-value") + 1] == "sid=abc123"
    assert "Authorization: Bearer eyJ0" in args


def test_wapiti_excludes_the_control_plane_and_logout(tmp_path):
    ctx = make_ctx(tmp_path, "wapiti", "default", [SHOP], creds={"shopfront": JSON_CREDS})
    args = WapitiDriver().plan(ctx)[0].args
    excluded = [args[i + 1] for i, a in enumerate(args) if a == "--exclude"]
    assert "http://shopfront-web:3000/__bench__" in excluded
    assert "http://shopfront-web:3000/api/auth/logout" in excluded


# -- nikto -----------------------------------------------------------------------


def test_nikto_is_told_to_stop_before_the_budget_kills_it(tmp_path):
    """nikto only serialises its report at close: killed, it leaves nothing at all."""
    ctx = make_ctx(tmp_path, "nikto", "default", [LEGACY], budget=Budget(wall_clock_s=1000))
    args = NiktoDriver().plan(ctx)[0].args
    assert int(args[args.index("-maxtime") + 1]) == 800


def test_nikto_never_prompts_and_never_passes_removed_flags(tmp_path):
    args = NiktoDriver().plan(make_ctx(tmp_path, "nikto", "default", [LEGACY]))[0].args
    assert args[args.index("-ask") + 1] == "no"
    assert "-nointeractive" in args
    # -no404 was removed from nikto's option table in 2.6.x: passing it aborts.
    assert "-no404" not in args
    # No -Tuning: choosing which test classes a scanner runs is the harness taking a
    # position on what the tool should look for.
    assert "-Tuning" not in args


def test_nikto_replays_the_session_with_add_header(tmp_path):
    ctx = make_ctx(tmp_path, "nikto", "default", [SHOP], sessions={"shopfront": SESSION})
    args = NiktoDriver().plan(ctx)[0].args
    headers = [args[i + 1] for i, a in enumerate(args) if a == "-Add-header"]
    assert "Cookie: sid=abc123" in headers


def test_nikto_basic_auth_uses_id(tmp_path):
    basic = Credentials(app="legacy", kind="basic", username="admin", password="pw",
                        verify_url="http://legacy-web/")
    ctx = make_ctx(tmp_path, "nikto", "default", [LEGACY], creds={"legacy": basic})
    args = NiktoDriver().plan(ctx)[0].args
    assert args[args.index("-id") + 1] == "admin:pw"


# -- skipfish --------------------------------------------------------------------


def skipfish_script(inv):
    assert inv.args[0] == "-c"
    return inv.args[1]


def test_skipfish_output_directory_must_not_exist_yet(tmp_path):
    """skipfish rmdir()s its -o argument: pointing it at the bind mount is fatal."""
    ctx = make_ctx(tmp_path, "skipfish", "default", [LEGACY])
    inv = SkipfishDriver().plan(ctx)[0]
    script = skipfish_script(inv)
    assert "-o /bench/raw/skipfish-legacy" in script
    assert not (ctx.raw_dir / "skipfish-legacy").exists()


def test_skipfish_copies_the_wordlist_because_it_rewrites_it(tmp_path):
    """-W mutates the dictionary in place; runs would stop being comparable."""
    script = skipfish_script(SkipfishDriver().plan(make_ctx(tmp_path, "skipfish", "default", [LEGACY]))[0])
    assert "cp /usr/share/skipfish/dictionaries/complete.wl /tmp/bench.wl" in script
    assert "-W /tmp/bench.wl" in script


def test_skipfish_time_limit_always_has_three_fields(tmp_path):
    """`-k 600` means six hundred HOURS: the multipliers are applied left to right."""
    ctx = make_ctx(tmp_path, "skipfish", "default", [LEGACY], budget=Budget(wall_clock_s=3600))
    script = skipfish_script(SkipfishDriver().plan(ctx)[0])
    tokens = shlex.split(script.split("exec ", 1)[1])
    assert tokens[tokens.index("-k") + 1] == "0:51:0"


def test_skipfish_blacklists_the_control_plane_and_logout(tmp_path):
    ctx = make_ctx(tmp_path, "skipfish", "default", [LEGACY], creds={"legacy": FORM_CREDS})
    script = skipfish_script(SkipfishDriver().plan(ctx)[0])
    tokens = shlex.split(script.split("exec ", 1)[1])
    excluded = [tokens[i + 1] for i, t in enumerate(tokens) if t == "-X"]
    assert "/__bench__" in excluded and "/logout.php" in excluded


def test_skipfish_uses_a_fixed_seed(tmp_path):
    """Two runs of the same version against the same target must crawl alike."""
    script = skipfish_script(SkipfishDriver().plan(make_ctx(tmp_path, "skipfish", "default", [LEGACY]))[0])
    assert "-q 0x7074616173" in script


def test_skipfish_interpolations_are_shell_quoted(tmp_path):
    """A target URL must never be able to turn the harness into a shell."""
    nasty = AppSpec(key="evil", services=["x"], base_url="http://x/;touch /tmp/pwned")
    script = skipfish_script(SkipfishDriver().plan(make_ctx(tmp_path, "skipfish", "default", [nasty]))[0])
    assert "touch /tmp/pwned" not in script.replace("'http://x/;touch /tmp/pwned'", "")


# -- generic ---------------------------------------------------------------------


def test_generic_starts_no_container(tmp_path):
    ctx = make_ctx(tmp_path, "generic", "import", [SHOP])
    assert GenericDriver().plan(ctx) == []


def test_generic_ingests_the_vendor_file(tmp_path, fixtures):
    ctx = make_ctx(tmp_path, "generic", "import", [SHOP])
    ctx.options = {"findings_file": str(fixtures / "generic-vendor.json")}
    results = GenericDriver().run(ctx)
    assert results[0].error is None
    assert (ctx.raw_dir / "generic-findings.json").exists()
    findings = GenericDriver().normalise(ctx.raw_dir)
    assert any(f.cwe == 89 for f in findings.findings)


def test_generic_without_a_file_is_an_explicit_failure(tmp_path):
    """Silently producing zero findings would read as "the vendor found nothing"."""
    ctx = make_ctx(tmp_path, "generic", "import", [SHOP])
    results = GenericDriver().run(ctx)
    assert "no --findings file" in results[0].error


def test_generic_attach_waits_for_the_sentinel(tmp_path, fixtures):
    ctx = make_ctx(tmp_path, "generic", "attach", [SHOP], budget=Budget(wall_clock_s=3600, poll_interval_s=5))
    ctx.options = {"attach": True, "findings_file": str(fixtures / "generic-vendor.json")}
    # The vendor drops the sentinel when their scan finishes, instead of the
    # operator sitting out the whole budget.
    (ctx.run_dir / SENTINEL).write_text("done")
    results = GenericDriver().run(ctx)
    assert results[0].stop_reason == "completed"


def test_generic_attach_stops_at_the_budget(tmp_path):
    ctx = make_ctx(tmp_path, "generic", "attach", [SHOP], budget=Budget(wall_clock_s=30, poll_interval_s=5))
    ctx.options = {"attach": True}
    results = GenericDriver().run(ctx)
    assert results[0].stop_reason == "budget_wall_clock"


# -- uniformity ------------------------------------------------------------------


@pytest.mark.parametrize(
    "driver", [ZapDriver(), NucleiDriver(), WapitiDriver(), NiktoDriver(), SkipfishDriver(), GenericDriver()]
)
def test_every_driver_exposes_the_same_interface(driver):
    assert callable(driver.run) and callable(driver.normalise) and callable(driver.plan)
    assert isinstance(driver.key, str) and driver.key


@pytest.mark.parametrize(
    "driver", [ZapDriver(), NucleiDriver(), WapitiDriver(), NiktoDriver(), SkipfishDriver()]
)
def test_the_version_probe_matches_the_image_entrypoint(driver):
    """A probe that ignores the driver's entrypoint silently records no version.

    skipfish is the case that bites: its container is entered through a shell, so
    its version command has to be a shell command too.
    """
    assert driver.version_command
    if driver.default_entrypoint:
        assert driver.version_entrypoint == driver.default_entrypoint


def test_skipfish_version_comes_from_the_recorded_package_version():
    """Upstream has said "2.10b" since 2012; the Kali package version is the real one."""
    assert "/skipfish.version" in " ".join(SkipfishDriver().version_command)


def test_the_default_port_is_never_spelled_out_in_a_target_url():
    """`http://host:80` silently matches nothing in a ZAP context."""
    from runners._lib.config import BenchConfig

    app = AppSpec(key="x", services=["x"], base_url="http://legacy-web:80")
    assert app.base_url == "http://legacy-web"
    for spec in BenchConfig.load().apps.values():
        assert not spec.base_url.endswith(":80")
