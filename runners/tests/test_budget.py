"""Budget parsing and enforcement.

A comparison table is only meaningful if every column cost the same. These tests
cover the three ways a run ends -- the tool finished, the clock ran out, the request
cap was hit -- plus the two things that make the result honest afterwards: the tool
gets a grace period to write its report, and being cut off is recorded as such so
the number is read as a lower bound.
"""

from __future__ import annotations

import pytest

from runners._lib.budget import Budget, BudgetWatch, StopReason, parse_duration
from runners._lib.config import AppSpec, ToolSpec
from runners._lib.driver import BaseDriver, Invocation, RunContext

from fakes import FakeClock, FakeDocker

TOOL = ToolSpec(key="stub", image="example/stub:1")
APPS = [
    AppSpec(key="shopfront", services=["shopfront-web"], base_url="http://shopfront-web:3000"),
    AppSpec(key="blog", services=["blog-web"], base_url="http://blog-web:8000"),
]


class StubDriver(BaseDriver):
    key = "stub"

    def __init__(self, artifacts=()):
        self.artifacts = list(artifacts)

    def plan(self, ctx: RunContext):
        return [
            Invocation(name=app.key, app=app.key, args=["--target", app.base_url],
                       artifacts=self.artifacts)
            for app in ctx.apps
        ]


def make_ctx(tmp_path, docker, clock, budget, apps=None, meter=None):
    return RunContext(
        run_id="run-0123456789",
        tool=TOOL,
        profile="default",
        apps=apps if apps is not None else APPS[:1],
        budget=budget,
        run_dir=tmp_path,
        docker=docker,
        sleep=clock.sleep,
        clock=clock,
        request_meter=meter or (lambda: 0),
    )


# -- parsing ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,seconds", [("45m", 2700), ("2h", 7200), ("900", 900), ("1d", 86400), ("30s", 30)]
)
def test_duration_parsing(text, seconds):
    assert parse_duration(text) == seconds


def test_a_nonsense_duration_is_rejected_loudly():
    """Silently defaulting a typo'd budget would publish an incomparable number."""
    with pytest.raises(ValueError):
        parse_duration("fast")


def test_budget_is_serialisable_for_the_record():
    budget = Budget.parse(wall_clock="45m", max_requests=20000)
    assert budget.to_dict() == {
        "wall_clock_s": 2700, "max_requests": 20000, "grace_s": 30, "poll_interval_s": 5.0,
    }
    assert "2700s wall clock" in budget.describe()


# -- the watch -------------------------------------------------------------------


def test_wall_clock_fires_at_the_deadline():
    clock = FakeClock()
    watch = BudgetWatch(Budget(wall_clock_s=100, max_requests=None), clock=clock)
    assert watch.check().reason is None
    clock.advance(99)
    assert watch.check().reason is None
    clock.advance(1)
    assert watch.check().reason is StopReason.BUDGET_WALL_CLOCK


def test_request_cap_fires():
    watch = BudgetWatch(Budget(wall_clock_s=None, max_requests=500), clock=FakeClock())
    assert watch.check(499).reason is None
    assert watch.check(500).reason is StopReason.BUDGET_REQUESTS


def test_request_counter_never_goes_backwards():
    """A transient collector error must not hand the tool extra budget."""
    watch = BudgetWatch(Budget(wall_clock_s=None, max_requests=500), clock=FakeClock())
    watch.check(499)
    assert watch.check(0).requests == 499  # collector hiccup returns 0
    assert watch.check(501).reason is StopReason.BUDGET_REQUESTS


def test_exhausted_reasons_are_flagged_as_lower_bounds():
    assert StopReason.BUDGET_WALL_CLOCK.exhausted
    assert StopReason.BUDGET_REQUESTS.exhausted
    assert not StopReason.COMPLETED.exhausted


# -- enforcement in the run loop -------------------------------------------------


def test_tool_is_stopped_when_the_clock_runs_out(tmp_path):
    clock = FakeClock()
    docker = FakeDocker()  # never exits on its own
    ctx = make_ctx(tmp_path, docker, clock, Budget(wall_clock_s=60, poll_interval_s=5, grace_s=30))
    results = StubDriver().run(ctx)
    assert [r.stop_reason for r in results] == [StopReason.BUDGET_WALL_CLOCK.value]
    assert docker.stopped == ["scan-stub-shopfront-run-0123"]
    assert results[0].elapsed_s >= 60


def test_tool_is_stopped_when_the_request_cap_is_hit(tmp_path):
    clock = FakeClock()
    docker = FakeDocker()
    counter = {"n": 0}

    def meter():
        counter["n"] += 400
        return counter["n"]

    ctx = make_ctx(
        tmp_path, docker, clock,
        Budget(wall_clock_s=3600, max_requests=1000, poll_interval_s=5),
        meter=meter,
    )
    results = StubDriver().run(ctx)
    assert results[0].stop_reason == StopReason.BUDGET_REQUESTS.value
    assert docker.stopped, "the container must actually be stopped, not just recorded"


def test_a_tool_that_finishes_early_is_not_stopped(tmp_path):
    """`completed` and `cut off` are different results and must not be conflated."""
    clock = FakeClock()
    docker = FakeDocker(runs_for_polls={"scan-stub-shopfront-run-0123": 2})
    ctx = make_ctx(tmp_path, docker, clock, Budget(wall_clock_s=3600, poll_interval_s=5))
    results = StubDriver().run(ctx)
    assert results[0].stop_reason == StopReason.COMPLETED.value
    assert docker.stopped == []
    assert docker.removed == ["scan-stub-shopfront-run-0123"]


def test_the_stop_uses_the_grace_period_from_the_budget(tmp_path, monkeypatch):
    """ZAP and skipfish write their report on shutdown; a SIGKILL loses the run."""
    clock = FakeClock()
    docker = FakeDocker()
    seen = {}
    original = docker.stop

    def spy(container_id, *, grace):
        seen["grace"] = grace
        return original(container_id, grace=grace)

    monkeypatch.setattr(docker, "stop", spy)
    ctx = make_ctx(tmp_path, docker, clock, Budget(wall_clock_s=30, grace_s=45, poll_interval_s=5))
    StubDriver().run(ctx)
    assert seen["grace"] == 45


def test_budget_is_split_across_targets_and_the_split_is_recorded(tmp_path):
    clock = FakeClock()
    docker = FakeDocker(
        runs_for_polls={
            "scan-stub-shopfront-run-0123": 1,
            "scan-stub-blog-run-0123": 1,
        }
    )
    ctx = make_ctx(
        tmp_path, docker, clock, Budget(wall_clock_s=600, poll_interval_s=5), apps=APPS
    )
    results = StubDriver().run(ctx)
    assert [r.app for r in results] == ["shopfront", "blog"]
    assert results[0].budget_share_s == pytest.approx(300, abs=1)
    # Time the first target did not use is inherited by the second.
    assert results[1].budget_share_s > 300


def test_each_target_gets_its_own_share_of_the_wall_clock(tmp_path):
    """Two targets, one hour: half an hour each, and neither overruns the total."""
    clock = FakeClock()
    docker = FakeDocker()  # neither container exits on its own
    ctx = make_ctx(
        tmp_path, docker, clock, Budget(wall_clock_s=60, poll_interval_s=5), apps=APPS
    )
    results = StubDriver().run(ctx)
    assert [r.stop_reason for r in results] == [StopReason.BUDGET_WALL_CLOCK.value] * 2
    assert len(docker.stopped) == 2
    assert sum(r.elapsed_s for r in results) <= 60 + ctx.budget.poll_interval_s


def test_targets_not_scanned_because_the_budget_ran_out_are_recorded_as_such(tmp_path):
    """Otherwise an unscanned target looks exactly like a target with no findings.

    Here the first target burns the whole *request* budget, which is global rather
    than per-target: a tool that spends 20 000 requests re-fuzzing one parameter on
    shopfront does not then get a fresh allowance on blog.
    """
    clock = FakeClock()
    docker = FakeDocker()
    counter = {"n": 0}

    def meter():
        counter["n"] += 600
        return counter["n"]

    ctx = make_ctx(
        tmp_path, docker, clock,
        Budget(wall_clock_s=600, max_requests=1000, poll_interval_s=5),
        apps=APPS, meter=meter,
    )
    results = StubDriver().run(ctx)
    assert len(results) == 2
    assert results[0].stop_reason == StopReason.BUDGET_REQUESTS.value
    assert results[1].container_id is None, "the second target must not be started"
    assert "budget exhausted" in results[1].error
    assert results[1].stop_reason == StopReason.BUDGET_REQUESTS.value


def test_a_missing_report_is_visible_in_the_result(tmp_path):
    """"no report" and "no findings" must not look the same in the record."""
    clock = FakeClock()
    docker = FakeDocker(runs_for_polls={"scan-stub-shopfront-run-0123": 1})
    ctx = make_ctx(tmp_path, docker, clock, Budget(wall_clock_s=600, poll_interval_s=5))
    results = StubDriver(artifacts=["stub-shopfront.json"]).run(ctx)
    assert results[0].artifacts_present == {"stub-shopfront.json": False}


def test_only_raw_and_conf_are_mounted_and_conf_is_read_only(tmp_path):
    """The tool gets the two directories it needs and nothing else.

    The run record and the normalised findings stay outside the container, so a tool
    cannot overwrite the evidence it is judged on -- and an agentic tool reading its
    own filesystem finds a working directory rather than a grading harness.
    """
    clock = FakeClock()
    docker = FakeDocker(runs_for_polls={"scan-stub-shopfront-run-0123": 1})
    ctx = make_ctx(tmp_path, docker, clock, Budget(wall_clock_s=600, poll_interval_s=5))
    StubDriver().run(ctx)
    volumes = docker.started[0]["volumes"]
    assert (str(ctx.raw_dir), "/work/raw") in volumes
    assert (str(ctx.conf_dir), "/work/conf:ro") in volumes
    assert not any(host == str(tmp_path) for host, _ in volumes)
    # Scanner images run as assorted uids; an unwritable mount means no report at all.
    assert (ctx.raw_dir.stat().st_mode & 0o777) == 0o777


def test_the_mount_path_names_nothing(tmp_path):
    """A directory called /bench would tell an agentic tool what it is inside of."""
    clock = FakeClock()
    docker = FakeDocker(runs_for_polls={"scan-stub-shopfront-run-0123": 1})
    ctx = make_ctx(tmp_path, docker, clock, Budget(wall_clock_s=600, poll_interval_s=5))
    StubDriver().run(ctx)
    for _, container_path in docker.started[0]["volumes"]:
        assert container_path.startswith("/work")
    assert "bench" not in docker.started[0]["name"]


def test_the_tool_is_attached_to_bench_public_only(tmp_path):
    """One network. Anything else and the scanner can reach the answer key."""
    clock = FakeClock()
    docker = FakeDocker(runs_for_polls={"scan-stub-shopfront-run-0123": 1})
    ctx = make_ctx(tmp_path, docker, clock, Budget(wall_clock_s=600, poll_interval_s=5))
    StubDriver().run(ctx)
    assert docker.started[0]["network"] == "bench-public"
