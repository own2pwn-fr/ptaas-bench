"""Target reset, and the proof that it happened.

Why this module is paranoid: the corpus contains stored XSS, stored SQL injection,
mass assignment and other state-changing flaws. If one tool stores a payload and the
app is not restored, the next tool is credited with the first one's finding, and
nothing in the results says so -- the numbers just come out flattering.

**Reset is a command inside the container, never an HTTP endpoint.** This is fixed by
targets/target-contract.yaml and there are two reasons for it, both of which bite:

* Targets are dual-homed and serve one listener, so a route "only on the internal
  network" is in practice reachable from the tool's network too. A reset endpoint
  would let the scanner under test wipe state mid-run -- and would itself be an
  unplanted vulnerability sitting in the middle of the corpus.
* A request path like ``/__bench__/seed`` on the wire tells any tool that looks at it
  that it is being graded. The whole platform is deception-graded; an agent that
  works out it is inside an evaluation stops behaving like an attacker.

So: ``docker compose exec <service> /usr/local/bin/state-reset``. It is idempotent,
exits non-zero on failure, and prints a one-line state digest that changes if and
only if the seeded state changed. The orchestrator records that digest before and
after every run and refuses to open the next run until it is back to its seeded
value.

Restarting containers is kept as a per-application option rather than the mechanism.
It is still the only way to clear state that lives outside the application process --
a poisoned Varnish cache on the `edge` target is in Varnish's memory, and no script
inside the origin can flush it.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .config import AppSpec

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
    state_digest: str | None = None
    reference_digest: str | None = None
    checks: list[Check] = field(default_factory=list)
    duration_s: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "app": self.app,
            "ok": self.ok,
            "state_digest": self.state_digest,
            "reference_digest": self.reference_digest,
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


# The line may be labelled -- the `edge` target prints `state <32 hex chars>` -- so
# the whole line is the digest and the test is whether it *contains* a token that
# could be one. A token short enough to be an English word cannot be: a digest has to
# change whenever the seeded state changes, and accepting "done!" or "restored" would
# reintroduce exactly the silent pass this module exists to prevent.
_DIGEST_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9:_+/=.-]{11,}")


def extract_digest(stdout: str) -> str | None:
    """The one-line state digest the reset command prints.

    The last line carrying a digest-shaped token, whitespace-normalised, so that a
    script which logs progress first still works and a labelled digest survives
    intact. Comparing whole lines across runs is deliberate: the label is as stable
    as the digest, and a label that changed would itself be a change of behaviour.
    """
    for line in reversed((stdout or "").splitlines()):
        candidate = " ".join(line.split())
        if candidate and _DIGEST_TOKEN_RE.search(candidate):
            return candidate
    return None


class TargetResetter:
    """Runs the target's reset command and proves the state came back seeded."""

    def __init__(
        self,
        docker: Any,
        *,
        health_timeout_s: float = 120,
        poll_interval_s: float = 2.0,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.docker = docker
        self.health_timeout_s = health_timeout_s
        self.poll_interval_s = poll_interval_s
        self._sleep = sleep
        self._clock = clock

    # -- the reset ---------------------------------------------------------------

    def reset(self, app: AppSpec, *, reference_digest: str | None = None) -> ResetOutcome:
        started = self._clock()
        outcome = ResetOutcome(app=app.key, ok=False, reference_digest=reference_digest)

        services = app.services_to_restart
        if services:
            # The reset command's digest is computed over persistent storage, so it
            # cannot see state held in the process: a polluted prototype, an
            # in-memory cache, a cached object in a proxy. Only a restart clears
            # those, and the digest would come back identical either way.
            before = {svc: self.docker.container_started_at(self._cid(svc)) for svc in services}
            self.docker.compose_restart(services)
            after = {svc: self.docker.container_started_at(self._cid(svc)) for svc in services}
            for svc in services:
                outcome.checks.append(
                    Check(
                        f"restarted:{svc}",
                        _restart_advanced(before.get(svc), after.get(svc)),
                        f"StartedAt {before.get(svc)!r} -> {after.get(svc)!r}",
                    )
                )
            healthy, detail = self.wait_healthy(app)
            outcome.checks.append(Check("health", healthy, detail))
            if not healthy:
                outcome.duration_s = self._clock() - started
                return outcome

        res = self.run_reset_command(app)
        ran = res.returncode == 0
        outcome.checks.append(
            Check("reset_command", ran, f"exit {res.returncode} {res.stderr.strip()[:200]}".strip())
        )
        if not ran:
            outcome.duration_s = self._clock() - started
            return outcome

        outcome.state_digest = extract_digest(res.stdout)
        outcome.checks.append(
            Check(
                "digest_printed",
                outcome.state_digest is not None,
                f"stdout {res.stdout.strip()[:200]!r}",
            )
        )

        if reference_digest is None:
            # Nothing to compare against yet. Recorded as a *failed* check in strict
            # terms would block every first run; recorded as passing silently would
            # hide that no comparison happened. So it passes, and says why.
            outcome.checks.append(
                Check(
                    "digest_matches_reference",
                    True,
                    f"no reference digest yet; recording {outcome.state_digest!r} as the "
                    "seeded value for the next run to check against",
                )
            )
        else:
            outcome.checks.append(
                Check(
                    "digest_matches_reference",
                    outcome.state_digest == reference_digest,
                    f"expected {reference_digest!r}, got {outcome.state_digest!r}",
                )
            )

        outcome.ok = all(c.ok for c in outcome.checks)
        outcome.duration_s = self._clock() - started
        return outcome

    def run_reset_command(self, app: AppSpec) -> Any:
        return self.docker.compose_exec(
            app.reset_service, [app.reset_command], timeout=app.reset_timeout_s
        )

    def digest_after_run(self, app: AppSpec) -> tuple[str | None, str]:
        """Re-run the reset once the tool has stopped, and report the digest.

        Two jobs at once: it leaves the target clean for whoever runs next, and it
        proves the command is deterministic. A digest that differs from the one taken
        before the run means the reset does not restore the same state twice, and
        every comparison against this target is then suspect.
        """
        res = self.run_reset_command(app)
        if res.returncode != 0:
            return None, f"exit {res.returncode} {res.stderr.strip()[:200]}".strip()
        return extract_digest(res.stdout), "ok"

    # -- health ------------------------------------------------------------------

    def wait_healthy(self, app: AppSpec) -> tuple[bool, str]:
        """Wait for the container healthcheck, not for an HTTP probe of our own.

        The harness deliberately sends no traffic of its own to a target's
        application port outside the login flow: docker already knows whether the
        container is healthy, and asking it costs the target nothing and reveals
        nothing.
        """
        services = app.health_services or app.restart_services
        if not services:
            return True, "no health services configured"
        deadline = self._clock() + self.health_timeout_s
        last = "never reported"
        while self._clock() < deadline:
            states = {svc: self.docker.container_health(self._cid(svc)) for svc in services}
            # "none" means the image declares no healthcheck; running is then the
            # only signal available, and pretending otherwise would hang every run.
            if all(state in ("healthy", "none") for state in states.values()):
                return True, ", ".join(f"{k}={v}" for k, v in states.items())
            last = ", ".join(f"{k}={v}" for k, v in states.items())
            self._sleep(self.poll_interval_s)
        return False, f"unhealthy after {self.health_timeout_s}s ({last})"

    def _cid(self, service: str) -> str:
        return self.docker.compose_ps_id(service) or service


def reset_targets(
    resetter: TargetResetter,
    apps: list[AppSpec],
    *,
    reference_digests: dict[str, str] | None = None,
    strict: bool = True,
) -> list[ResetOutcome]:
    """Reset every app in scope. Raises unless every reset is provably clean."""
    reference_digests = reference_digests or {}
    outcomes = [
        resetter.reset(app, reference_digest=reference_digests.get(app.key)) for app in apps
    ]
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
