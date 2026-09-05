"""Target reset verification.

This is the test file that protects the headline claim of the whole benchmark. If
the target is not rebuilt between tools, a stored XSS planted by ZAP is found by the
next scanner and credited to it, and nothing in the output reveals it. So a reset
that cannot be *proven* must abort the run, and every way it can fail silently gets
a test here.
"""

from __future__ import annotations

import pytest

from runners._lib.config import AppSpec
from runners._lib.internal_http import Response
from runners._lib.reset import ResetError, TargetResetter, reset_targets

from fakes import FakeClock, FakeDocker, FakeHttp, json_response

APP = AppSpec(key="shopfront", services=["shopfront-web"], base_url="http://shopfront-web:3000")

HEALTH = "http://shopfront-web:3000/healthz"
SEED = "http://shopfront-web:3000/__bench__/seed"
STATE = "http://shopfront-web:3000/__bench__/state"


def make_resetter(http: FakeHttp, docker: FakeDocker, clock: FakeClock | None = None):
    clock = clock or FakeClock()
    return TargetResetter(docker, http, sleep=clock.sleep, clock=clock, health_timeout_s=30)


def healthy_routes(seed_id="seed-2", seed_digest="sha256:aaa", state_digest="sha256:aaa",
                   dirty=False):
    return {
        HEALTH: Response(200, "ok"),
        SEED: json_response({"seed_id": seed_id, "state_digest": seed_digest}),
        STATE: json_response({"seed_id": seed_id, "state_digest": state_digest, "dirty": dirty}),
    }


def test_clean_reset_passes_every_check():
    docker = FakeDocker(started_at={"cid-shopfront-web": "2026-09-05T17:00:00.000000000Z"})
    resetter = make_resetter(FakeHttp(healthy_routes()), docker)
    outcome = resetter.reset(APP, previous_seed_id="seed-1")
    assert outcome.ok, outcome.failures
    assert {c.name for c in outcome.checks} == {
        "restarted:shopfront-web", "health", "seeded", "seed_id_changed",
        "state_matches_seed", "state_clean",
    }
    assert outcome.seed_id == "seed-2"


def test_a_restart_that_did_not_restart_is_caught():
    """`docker compose restart` returning 0 is not evidence the container restarted."""
    docker = FakeDocker(
        started_at={"cid-shopfront-web": "2026-09-05T17:00:00.000000000Z"},
        restart_advances_clock=False,
    )
    resetter = make_resetter(FakeHttp(healthy_routes()), docker)
    outcome = resetter.reset(APP, previous_seed_id="seed-1")
    assert not outcome.ok
    assert [c.name for c in outcome.failures] == ["restarted:shopfront-web"]


def test_a_repeated_seed_id_means_nothing_was_reseeded():
    """A seed endpoint returning the same id twice has rebuilt nothing."""
    docker = FakeDocker(started_at={"cid-shopfront-web": "2026-09-05T17:00:00.000000000Z"})
    resetter = make_resetter(FakeHttp(healthy_routes(seed_id="seed-1")), docker)
    outcome = resetter.reset(APP, previous_seed_id="seed-1")
    assert not outcome.ok
    assert [c.name for c in outcome.failures] == ["seed_id_changed"]


def test_state_digest_must_match_the_freshly_seeded_one():
    """The app's own view of its state has to agree with what seeding just wrote."""
    docker = FakeDocker(started_at={"cid-shopfront-web": "2026-09-05T17:00:00.000000000Z"})
    routes = healthy_routes(seed_digest="sha256:aaa", state_digest="sha256:bbb")
    outcome = make_resetter(FakeHttp(routes), docker).reset(APP, previous_seed_id="seed-1")
    assert not outcome.ok
    assert [c.name for c in outcome.failures] == ["state_matches_seed"]


def test_dirty_state_after_seeding_fails():
    docker = FakeDocker(started_at={"cid-shopfront-web": "2026-09-05T17:00:00.000000000Z"})
    outcome = make_resetter(FakeHttp(healthy_routes(dirty=True)), docker).reset(
        APP, previous_seed_id="seed-1"
    )
    assert not outcome.ok
    assert [c.name for c in outcome.failures] == ["state_clean"]


def test_a_target_that_cannot_prove_cleanliness_is_not_assumed_clean():
    """An SDK too old to report `dirty` gets an unproven verdict, not the benefit of the doubt."""
    docker = FakeDocker(started_at={"cid-shopfront-web": "2026-09-05T17:00:00.000000000Z"})
    routes = healthy_routes()
    routes[STATE] = json_response({"seed_id": "seed-2", "state_digest": "sha256:aaa"})
    outcome = make_resetter(FakeHttp(routes), docker).reset(APP, previous_seed_id="seed-1")
    assert not outcome.ok
    assert [c.name for c in outcome.failures] == ["state_clean"]
    assert "cannot prove" in outcome.failures[0].detail


def test_health_is_polled_then_gives_up_without_seeding():
    """A target that never comes back must not be seeded and must not open a run."""
    docker = FakeDocker(started_at={"cid-shopfront-web": "2026-09-05T17:00:00.000000000Z"})
    http = FakeHttp({HEALTH: Response(503, "starting")})
    outcome = make_resetter(http, docker).reset(APP, previous_seed_id="seed-1")
    assert not outcome.ok
    assert [c.name for c in outcome.failures] == ["health"]
    assert not any(url == SEED for _, url in http.requests)


def test_health_recovers_after_a_few_polls():
    docker = FakeDocker(started_at={"cid-shopfront-web": "2026-09-05T17:00:00.000000000Z"})
    routes = healthy_routes()
    routes[HEALTH] = [Response(503, ""), Response(503, ""), Response(200, "ok")]
    outcome = make_resetter(FakeHttp(routes), docker).reset(APP, previous_seed_id="seed-1")
    assert outcome.ok, outcome.failures


def test_reset_targets_refuses_to_open_a_run_on_failure():
    """The whole point: an unverifiable reset aborts before the collector run opens."""
    docker = FakeDocker(
        started_at={"cid-shopfront-web": "2026-09-05T17:00:00.000000000Z"},
        restart_advances_clock=False,
    )
    resetter = make_resetter(FakeHttp(healthy_routes()), docker)
    with pytest.raises(ResetError, match="refusing to open a run"):
        reset_targets(resetter, [APP], previous_seed_ids={"shopfront": "seed-1"})


def test_control_requests_are_flagged_synthetic():
    """Seeding traffic must never be scored as a tool's crawl coverage."""
    assert APP.control_headers["X-Bench-Selftest"] == "1"
