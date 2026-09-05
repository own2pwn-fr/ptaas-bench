"""Target reset verification.

This file protects the headline claim of the whole benchmark. If a target is not
restored between tools, a payload stored by one scanner is found by the next and
credited to it, and nothing in the output reveals it -- the numbers just come out
flattering.

Reset is a command inside the container (targets/target-contract.yaml), never an HTTP
endpoint, for two reasons this suite also covers: an HTTP reset would be reachable by
the tool under test, and a path like `/__bench__/seed` on the wire would tell any tool
that looks at it that it is being graded.
"""

from __future__ import annotations

import pytest

from runners._lib.config import AppSpec
from runners._lib.dockerctl import ExecResult
from runners._lib.reset import ResetError, TargetResetter, extract_digest, reset_targets

from fakes import FakeClock, FakeDocker, reset_ok

APP = AppSpec(
    key="shopfront",
    services=["shopfront-api", "shopfront-db"],
    base_url="http://www.kestrelgoods.example",
    internal_url="http://shopfront-api:3000",
    reset_service="shopfront-api",
)
CACHED = AppSpec(
    key="edge",
    services=["edge-nginx", "edge-varnish", "edge-origin"],
    base_url="http://www.halyardsupply.net",
    internal_url="http://nginx",
    reset_service="edge-origin",
    restart_services=["edge-varnish"],
)


def make_resetter(docker: FakeDocker, clock: FakeClock | None = None) -> TargetResetter:
    clock = clock or FakeClock()
    return TargetResetter(docker, sleep=clock.sleep, clock=clock, health_timeout_s=30)


# -- the digest ------------------------------------------------------------------


def test_digest_is_the_last_digest_bearing_line():
    """A reset script may log progress before printing its result."""
    assert extract_digest("rebuilding\nseeding 42 rows\nsha256:abcdef12\n") == "sha256:abcdef12"
    assert extract_digest("") is None
    # Prose is not a digest, and neither is a short word: a digest has to change
    # whenever the seeded state changes, and "done!" cannot.
    assert extract_digest("all done, state restored") is None
    assert extract_digest("done!") is None


def test_a_labelled_digest_survives_intact():
    """The `edge` target prints `state <32 hex>`; the label is part of the value."""
    line = "state 9f1c0b7d4e2a6538bb10c4d7e9a2f6c1"
    assert extract_digest(f"reset: 3 sessions dropped\n{line}\n") == line


# -- the happy path --------------------------------------------------------------


def test_reset_runs_the_command_in_the_container_not_over_http():
    docker = FakeDocker(exec_results={"shopfront-api": reset_ok("sha256:seeded-1")})
    outcome = make_resetter(docker).reset(APP, reference_digest="sha256:seeded-1")
    assert outcome.ok, outcome.failures
    assert outcome.state_digest == "sha256:seeded-1"
    assert ("compose_exec", ("shopfront-api", ["/usr/local/bin/state-reset"])) in docker.calls


def test_first_ever_run_has_nothing_to_compare_against_and_says_so():
    docker = FakeDocker(exec_results={"shopfront-api": reset_ok("sha256:seeded-1")})
    outcome = make_resetter(docker).reset(APP, reference_digest=None)
    assert outcome.ok
    check = next(c for c in outcome.checks if c.name == "digest_matches_reference")
    assert "no reference digest yet" in check.detail


# -- the failures that would otherwise be silent ---------------------------------


def test_a_failed_reset_command_stops_the_run():
    docker = FakeDocker(
        exec_results={"shopfront-api": ExecResult(["exec"], 1, "", "postgres: connection refused")}
    )
    outcome = make_resetter(docker).reset(APP, reference_digest="sha256:seeded-1")
    assert not outcome.ok
    assert [c.name for c in outcome.failures] == ["reset_command"]
    assert "connection refused" in outcome.failures[0].detail


def test_a_reset_that_prints_no_digest_is_not_trusted():
    """Without a digest there is no evidence the state is the seeded one."""
    docker = FakeDocker(exec_results={"shopfront-api": ExecResult(["exec"], 0, "done!\n", "")})
    outcome = make_resetter(docker).reset(APP, reference_digest=None)
    assert not outcome.ok
    assert [c.name for c in outcome.failures] == ["digest_printed"]


def test_a_digest_that_does_not_match_the_seeded_value_stops_the_run():
    """The target is not in the state the previous tool was measured against."""
    docker = FakeDocker(exec_results={"shopfront-api": reset_ok("sha256:drifted")})
    outcome = make_resetter(docker).reset(APP, reference_digest="sha256:seeded-1")
    assert not outcome.ok
    failure = outcome.failures[0]
    assert failure.name == "digest_matches_reference"
    assert "sha256:seeded-1" in failure.detail and "sha256:drifted" in failure.detail


def test_reset_targets_refuses_to_open_a_run_on_failure():
    docker = FakeDocker(exec_results={"shopfront-api": reset_ok("sha256:drifted")})
    with pytest.raises(ResetError, match="refusing to open a run"):
        reset_targets(
            make_resetter(docker), [APP], reference_digests={"shopfront": "sha256:seeded-1"}
        )


# -- state that lives outside the application process ----------------------------


def test_configured_services_are_restarted_before_the_reset_command():
    """A poisoned cache is in the proxy's memory; no script inside the origin clears it."""
    docker = FakeDocker(
        started_at={"cid-edge-varnish": "2026-09-05T17:00:00.000000000Z"},
        exec_results={"edge-origin": reset_ok("sha256:seeded-edge")},
    )
    outcome = make_resetter(docker).reset(CACHED, reference_digest="sha256:seeded-edge")
    assert outcome.ok, outcome.failures
    assert ("compose_restart", ["edge-varnish"]) in docker.calls
    assert any(c.name == "restarted:edge-varnish" and c.ok for c in outcome.checks)


def test_a_restart_that_did_not_restart_is_caught():
    """`docker compose restart` exiting 0 is not evidence the container restarted."""
    docker = FakeDocker(
        started_at={"cid-edge-varnish": "2026-09-05T17:00:00.000000000Z"},
        restart_advances_clock=False,
        exec_results={"edge-origin": reset_ok("sha256:seeded-edge")},
    )
    outcome = make_resetter(docker).reset(CACHED, reference_digest="sha256:seeded-edge")
    assert not outcome.ok
    assert [c.name for c in outcome.failures] == ["restarted:edge-varnish"]


def test_health_is_read_from_docker_not_probed_over_http():
    """The harness puts no traffic of its own on a target's application port here."""
    docker = FakeDocker(
        started_at={"cid-edge-varnish": "2026-09-05T17:00:00.000000000Z"},
        health={"cid-edge-varnish": "starting", "cid-edge-origin": "healthy"},
        exec_results={"edge-origin": reset_ok("sha256:seeded-edge")},
    )
    outcome = make_resetter(docker).reset(CACHED, reference_digest="sha256:seeded-edge")
    assert not outcome.ok
    assert [c.name for c in outcome.failures] == ["health"]
    # And it never got as far as resetting an unhealthy target.
    assert not any(call[0] == "compose_exec" for call in docker.calls)


def test_an_image_without_a_healthcheck_does_not_hang_the_run():
    docker = FakeDocker(
        started_at={"cid-edge-varnish": "2026-09-05T17:00:00.000000000Z"},
        health={"cid-edge-varnish": "none", "cid-edge-origin": "none"},
        exec_results={"edge-origin": reset_ok("sha256:seeded-edge")},
    )
    assert make_resetter(docker).reset(CACHED, reference_digest="sha256:seeded-edge").ok


# -- after the run ---------------------------------------------------------------


def test_post_run_digest_proves_the_reset_is_deterministic():
    docker = FakeDocker(exec_results={"shopfront-api": reset_ok("sha256:seeded-1")})
    resetter = make_resetter(docker)
    before = resetter.reset(APP, reference_digest="sha256:seeded-1").state_digest
    after, detail = resetter.digest_after_run(APP)
    assert (before, after, detail) == ("sha256:seeded-1", "sha256:seeded-1", "ok")


def test_a_reset_that_returns_a_different_digest_each_time_is_visible():
    """Then the target does not come back to the same state twice, and no two runs
    against it are comparable."""
    docker = FakeDocker(
        exec_results={
            "shopfront-api": [reset_ok("state 9f1c0b7d4e2a"), reset_ok("state 0000deadbeef")]
        }
    )
    resetter = make_resetter(docker)
    before = resetter.reset(APP, reference_digest=None).state_digest
    after, _ = resetter.digest_after_run(APP)
    assert before != after


def test_post_run_reset_failure_is_reported_not_swallowed():
    docker = FakeDocker(
        exec_results={"shopfront-api": [reset_ok(), ExecResult(["exec"], 2, "", "disk full")]}
    )
    resetter = make_resetter(docker)
    resetter.reset(APP, reference_digest=None)
    digest, detail = resetter.digest_after_run(APP)
    assert digest is None and "disk full" in detail


# -- deception -------------------------------------------------------------------


def test_reset_puts_nothing_on_the_wire():
    """The whole reset path is docker calls. Nothing reaches the target's HTTP port,
    so there is no request a tool could observe and no path it could name."""
    docker = FakeDocker(exec_results={"shopfront-api": reset_ok()})
    make_resetter(docker).reset(APP, reference_digest=None)
    assert {call[0] for call in docker.calls} <= {"compose_exec", "compose_restart"}
