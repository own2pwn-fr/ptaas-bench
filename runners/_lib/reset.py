"""Target reset, and the proof that it happened.

Why this module is paranoid: the corpus contains stored XSS, stored SQL injection,
mass assignment and other state-changing flaws. If ZAP plants a stored payload and
the target is not wiped before nuclei runs, nuclei is credited with ZAP's finding.
That single mistake would invalidate every stored-vulnerability column in the
published table, and it is invisible in the results -- the numbers just come out
flattering.

So a reset is not "we asked docker to restart it". A reset is four assertions:

1. every service container really restarted (``State.StartedAt`` moved forward),
2. the app answers its health probe again,
3. the seed endpoint ran and returned a *new* seed id,
4. the app's own state digest matches the freshly-seeded digest and reports no
   residual mutations.

If any of those cannot be established, the run does not open. Refusing to benchmark
is always cheaper than publishing a number nobody can defend.

The control endpoints (``/__bench__/seed``, ``/__bench__/state``) are part of the
target SDK contract and must be served on bench-internal only: exposed on
bench-public, the tool under test could reset the target mid-scan, or read the
digest and infer ground truth.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .config import AppSpec
from .internal_http import Http

log = logging.getLogger("bench.runners.reset")


class ResetError(RuntimeError):
    """The target could not be proven clean. The run must not open."""


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "ok": self.ok, "detail": self.detail}


@dataclass
class ResetOutcome:
    app: str
    ok: bool
    seed_id: str | None = None
    state_digest: str | None = None
    checks: list[Check] = field(default_factory=list)
    duration_s: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "app": self.app,
            "ok": self.ok,
            "seed_id": self.seed_id,
            "state_digest": self.state_digest,
            "duration_s": round(self.duration_s, 2),
            "checks": [c.to_dict() for c in self.checks],
        }

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if not c.ok]


def _parse_docker_time(value: str | None) -> datetime | None:
    """Parse ``State.StartedAt``.

    Docker emits RFC3339 with nanosecond precision, which ``fromisoformat`` rejects
    on some Python versions, so the fraction is truncated to microseconds first.
    A zero value ("0001-01-01T00:00:00Z") means "never started".
    """
    if not value or value.startswith("0001-01-01"):
        return None
    text = value.replace("Z", "+00:00")
    if "." in text:
        head, _, tail = text.partition(".")
        digits = "".join(ch for ch in tail if ch.isdigit())[:6]
        offset = tail[len(digits) :] if len(tail) > len(digits) else ""
        offset = offset.lstrip("0123456789")
        text = f"{head}.{digits or '0'}{offset}"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _restart_advanced(before: str | None, after: str | None) -> bool:
    """True when ``after`` is strictly later than ``before``."""
    if after is None:
        return False
    if before is None:
        return True
    dt_before, dt_after = _parse_docker_time(before), _parse_docker_time(after)
    if dt_before is None or dt_after is None:
        # Unparsable timestamps: fall back to inequality. Weaker, but a changed
        # opaque string still proves the container is not the one we saw before.
        return before != after
    return dt_after > dt_before


class TargetResetter:
    """Restarts a target application and proves it came back pristine."""

    def __init__(
        self,
        docker: Any,
        http: Http,
        *,
        health_timeout_s: float = 120,
        poll_interval_s: float = 2.0,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.docker = docker
        self.http = http
        self.health_timeout_s = health_timeout_s
        self.poll_interval_s = poll_interval_s
        self._sleep = sleep
        self._clock = clock

    def reset(self, app: AppSpec, *, previous_seed_id: str | None = None) -> ResetOutcome:
        started = self._clock()
        outcome = ResetOutcome(app=app.key, ok=False)

        before = {svc: self.docker.container_started_at(self._cid(svc)) for svc in app.services}
        self.docker.compose_restart(app.services)
        after = {svc: self.docker.container_started_at(self._cid(svc)) for svc in app.services}

        for svc in app.services:
            advanced = _restart_advanced(before.get(svc), after.get(svc))
            outcome.checks.append(
                Check(
                    f"restarted:{svc}",
                    advanced,
                    f"StartedAt {before.get(svc)!r} -> {after.get(svc)!r}",
                )
            )

        healthy, health_detail = self._wait_healthy(app)
        outcome.checks.append(Check("health", healthy, health_detail))
        if not healthy:
            outcome.duration_s = self._clock() - started
            return outcome

        seed = self.http.request(
            "POST", app.seed_url, json_body={"reason": "pre-run reset"}, headers=app.control_headers, timeout=180
        )
        seeded = seed.ok
        outcome.checks.append(Check("seeded", seeded, f"HTTP {seed.status} {seed.error or ''}".strip()))
        if not seeded:
            outcome.duration_s = self._clock() - started
            return outcome

        seed_body = _safe_json(seed.body)
        outcome.seed_id = _str_or_none(seed_body.get("seed_id"))
        seed_digest = _str_or_none(seed_body.get("state_digest"))

        # A seed endpoint that returns the same id twice is either cached or a no-op;
        # either way we have no evidence the data was actually rebuilt.
        changed = outcome.seed_id is not None and outcome.seed_id != previous_seed_id
        outcome.checks.append(
            Check(
                "seed_id_changed",
                changed,
                f"{previous_seed_id!r} -> {outcome.seed_id!r}",
            )
        )

        state = self.http.request("GET", app.state_url, headers=app.control_headers, timeout=60)
        state_body = _safe_json(state.body) if state.ok else {}
        outcome.state_digest = _str_or_none(state_body.get("state_digest"))
        digest_ok = bool(outcome.state_digest) and (
            seed_digest is None or outcome.state_digest == seed_digest
        )
        outcome.checks.append(
            Check(
                "state_matches_seed",
                digest_ok,
                f"seed={seed_digest!r} state={outcome.state_digest!r}",
            )
        )

        # `dirty` is the target's own answer to "has anything been written since the
        # seed?". Absent (older SDK), the check is skipped rather than assumed clean:
        # an unproven claim is recorded as unproven.
        if "dirty" in state_body:
            outcome.checks.append(
                Check("state_clean", not state_body.get("dirty"), f"dirty={state_body.get('dirty')!r}")
            )
        else:
            outcome.checks.append(
                Check("state_clean", False, "target reported no 'dirty' field; cannot prove cleanliness")
            )

        outcome.ok = all(c.ok for c in outcome.checks)
        outcome.duration_s = self._clock() - started
        return outcome

    def _cid(self, service: str) -> str:
        return self.docker.compose_ps_id(service) or service

    def _wait_healthy(self, app: AppSpec) -> tuple[bool, str]:
        deadline = self._clock() + self.health_timeout_s
        last = "never answered"
        while self._clock() < deadline:
            res = self.http.request("GET", app.health_url, headers=app.control_headers, timeout=10)
            if res.ok:
                return True, f"HTTP {res.status} after restart"
            last = f"HTTP {res.status} {res.error or ''}".strip()
            self._sleep(self.poll_interval_s)
        return False, f"unhealthy after {self.health_timeout_s}s ({last})"


def reset_targets(
    resetter: TargetResetter,
    apps: list[AppSpec],
    *,
    previous_seed_ids: dict[str, str] | None = None,
    strict: bool = True,
) -> list[ResetOutcome]:
    """Reset every app in scope. Raises unless every reset is provably clean."""
    previous_seed_ids = previous_seed_ids or {}
    outcomes = [resetter.reset(app, previous_seed_id=previous_seed_ids.get(app.key)) for app in apps]
    bad = [o for o in outcomes if not o.ok]
    if bad and strict:
        detail = "; ".join(
            f"{o.app}: " + ", ".join(f"{c.name} ({c.detail})" for c in o.failures) for o in bad
        )
        raise ResetError(
            "target reset could not be verified, refusing to open a run -- residual "
            f"state from the previous tool would be credited to this one. {detail}"
        )
    return outcomes


def _safe_json(body: str) -> dict[str, Any]:
    import json

    try:
        data = json.loads(body or "{}")
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _str_or_none(value: Any) -> str | None:
    return None if value is None else str(value)
