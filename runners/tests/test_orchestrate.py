"""The orchestrator's wiring: argument parsing, driver lookup, and the dry run.

The dry run is the reviewable artefact of this whole component. It generates every
config file the tools will be given and prints the exact container command lines,
without starting anything, so a driver can be audited before it is trusted with a
two-hour scan.
"""

from __future__ import annotations

import pytest

from runners._lib.config import ConfigError
from runners.orchestrate import build_parser, harness_commit, load_driver, main


def test_every_shipped_driver_is_loadable_by_name():
    for tool in ("zap", "nuclei", "wapiti", "nikto", "skipfish", "generic"):
        driver = load_driver(tool)
        assert driver.key == tool


def test_an_unknown_tool_lists_what_is_available():
    with pytest.raises(ConfigError, match="Available:"):
        load_driver("burpsuite")


def test_budget_arguments_reach_the_budget():
    args = build_parser().parse_args(
        ["--tool", "zap", "--budget", "45m", "--max-requests", "20000"]
    )
    assert args.budget == "45m" and args.max_requests == 20000


def test_reset_skipping_is_opt_in_and_flagged_as_debug_only():
    """It must be impossible to skip the reset by accident."""
    args = build_parser().parse_args(["--tool", "zap"])
    assert args.skip_reset is False
    assert args.allow_unverified_auth is False
    help_text = build_parser().format_help()
    assert "DEBUG ONLY" in help_text


def test_dry_run_writes_the_config_and_starts_nothing(tmp_path, capsys):
    code = main([
        "--tool", "zap", "--profile", "full", "--app", "shopfront",
        "--budget", "30m", "--dry-run", "--results-dir", str(tmp_path),
    ])
    assert code == 0
    out = capsys.readouterr().out
    assert "docker run --rm --network bench-public" in out
    assert "zap.sh -cmd -silent -autorun" in out
    plans = list(tmp_path.glob("dryrun-*/conf/zap-shopfront.yaml"))
    assert len(plans) == 1, "the generated plan is the artefact a reviewer reads"


def test_dry_run_covers_every_driver(tmp_path):
    for tool in ("zap", "nuclei", "wapiti", "nikto", "skipfish"):
        assert main([
            "--tool", tool, "--app", "legacy", "--budget", "10m",
            "--dry-run", "--results-dir", str(tmp_path / tool),
        ]) == 0


def test_an_unknown_profile_is_refused_before_anything_starts(tmp_path):
    code = main([
        "--tool", "zap", "--profile", "turbo", "--app", "shopfront",
        "--dry-run", "--results-dir", str(tmp_path),
    ])
    assert code == 1


def test_harness_commit_is_read_without_running_git():
    commit = harness_commit()
    assert commit is None or len(commit) == 40


def test_unset_driver_options_do_not_override_driver_defaults(tmp_path, capsys):
    """A present-but-None option is how `-l None` reaches a real command line."""
    main([
        "--tool", "skipfish", "--app", "legacy", "--budget", "30m",
        "--dry-run", "--results-dir", str(tmp_path),
    ])
    out = capsys.readouterr().out
    assert "None" not in out
    assert "-l 30" in out
    assert "cp /usr/share/skipfish/dictionaries/complete.wl /tmp/wordlist.wl" in out


def test_driver_options_are_passed_through_when_set(tmp_path, capsys):
    main([
        "--tool", "nuclei", "--app", "legacy", "--budget", "30m", "--rate-limit", "5",
        "--dry-run", "--results-dir", str(tmp_path),
    ])
    assert "-rate-limit 5" in capsys.readouterr().out


def test_dry_run_against_the_landed_target_shows_preparation_and_the_real_mounts(tmp_path, capsys):
    """The dry run is the reviewable artefact: it must match what will actually run."""
    code = main([
        "--tool", "nuclei", "--app", "edge", "--budget", "30m",
        "--dry-run", "--results-dir", str(tmp_path),
    ])
    assert code == 0
    out = capsys.readouterr().out
    # Preparation happens before the run, on a network that has egress.
    assert "--network bridge" in out and "-update-templates" in out
    # The scan itself is sealed onto the tool network with the two real mounts.
    assert "--network bench-public" in out
    assert "/work/raw" in out and "/work/conf:ro" in out
    # And it targets the URL the target itself declared, not one from apps.yaml.
    assert "http://www.halyardsupply.net" in out


def test_the_run_record_says_where_each_url_came_from(tmp_path):
    """A URL from an apps.yaml fallback is correct for one deployment only."""
    from runners.orchestrate import Orchestrator

    args = build_parser().parse_args(
        ["--tool", "zap", "--app", "edge", "--results-dir", str(tmp_path)]
    )
    orchestrator = Orchestrator(args)
    assert "edge" not in orchestrator.url_fallbacks


def test_reference_digests_come_from_the_previous_run(tmp_path):
    """The digest the last run recorded is what this run's reset must reproduce."""
    import json

    from runners.orchestrate import Orchestrator

    run_dir = tmp_path / "run-1"
    run_dir.mkdir()
    (run_dir / "run.json").write_text(
        json.dumps({"reset": [{"app": "edge", "state_digest": "state 9f1c0b7d4e2a"}]})
    )
    args = build_parser().parse_args(
        ["--tool", "zap", "--app", "edge", "--results-dir", str(tmp_path)]
    )
    assert Orchestrator(args)._reference_digests() == {"edge": "state 9f1c0b7d4e2a"}


def test_a_pinned_digest_in_apps_yaml_wins_over_the_previous_run(tmp_path):
    import json

    from runners.orchestrate import Orchestrator

    run_dir = tmp_path / "run-1"
    run_dir.mkdir()
    (run_dir / "run.json").write_text(
        json.dumps({"reset": [{"app": "edge", "state_digest": "state drifted00000"}]})
    )
    args = build_parser().parse_args(
        ["--tool", "zap", "--app", "edge", "--results-dir", str(tmp_path)]
    )
    orchestrator = Orchestrator(args)
    orchestrator.apps[0].expected_digest = "state pinned000000"
    assert orchestrator._reference_digests()["edge"] == "state pinned000000"
