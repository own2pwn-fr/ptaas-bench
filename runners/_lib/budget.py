"""The scan budget: an explicit, recorded, enforced limit on every run.

A comparison where one tool got ten minutes and another got six hours measures
nothing. So the budget is a first-class object: it is declared on the command line,
enforced by the orchestrator, and written verbatim into the run record next to the
findings, so a reader can see what each number cost.

Two dimensions, because tools fail differently:

* wall clock  -- catches the tool that hangs on one endpoint forever.
* request count -- catches the tool that spends its whole budget re-fuzzing the same
  parameter. Requests are counted by the *target*, via the collector, not by the
  tool's own log: a scanner's idea of how many requests it sent is not evidence.

The clock is injectable so the enforcement logic can be tested without sleeping.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any

_DURATION_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([smhd]?)\s*$", re.IGNORECASE)
_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "": 1}


def parse_duration(text: str | int | float | None) -> int | None:
    """'30m' -> 1800. A bare number is seconds."""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return int(text)
    match = _DURATION_RE.match(text)
    if not match:
        raise ValueError(f"cannot parse duration {text!r}; use e.g. 900, 30m, 2h")
    return int(float(match.group(1)) * _UNITS[match.group(2).lower()])


class StopReason(str, Enum):
    COMPLETED = "completed"  # the tool exited on its own
    BUDGET_WALL_CLOCK = "budget_wall_clock"
    BUDGET_REQUESTS = "budget_requests"
    INTERRUPTED = "interrupted"  # operator Ctrl-C
    ERROR = "error"

    @property
    def exhausted(self) -> bool:
        """True when the tool was cut off, i.e. its result is a lower bound."""
        return self in (StopReason.BUDGET_WALL_CLOCK, StopReason.BUDGET_REQUESTS)


@dataclass(frozen=True)
class Budget:
    """Limits for one run. ``None`` means unlimited on that axis."""

    wall_clock_s: int | None = 3600
    max_requests: int | None = None
    # Time the tool is given to flush its report after being asked to stop. Killing
    # ZAP instantly at the deadline throws away the findings the budget just bought.
    grace_s: int = 30
    poll_interval_s: float = 5.0

    @classmethod
    def parse(
        cls,
        wall_clock: str | int | None = "1h",
        max_requests: int | None = None,
        grace: str | int | None = 30,
        poll_interval_s: float = 5.0,
    ) -> Budget:
        return cls(
            wall_clock_s=parse_duration(wall_clock),
            max_requests=int(max_requests) if max_requests else None,
            grace_s=parse_duration(grace) or 0,
            poll_interval_s=poll_interval_s,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def describe(self) -> str:
        parts = []
        parts.append(f"{self.wall_clock_s}s wall clock" if self.wall_clock_s else "no time limit")
        if self.max_requests:
            parts.append(f"{self.max_requests} requests")
        return ", ".join(parts)


@dataclass
class BudgetState:
    elapsed_s: float
    requests: int
    reason: StopReason | None


class BudgetWatch:
    """Decides, at each poll, whether the tool may keep running."""

    def __init__(self, budget: Budget, clock: Callable[[], float] = time.monotonic):
        self.budget = budget
        self._clock = clock
        self.started = clock()
        self.requests = 0

    @property
    def elapsed(self) -> float:
        return self._clock() - self.started

    def remaining_s(self) -> float | None:
        if self.budget.wall_clock_s is None:
            return None
        return max(0.0, self.budget.wall_clock_s - self.elapsed)

    def check(self, requests: int | None = None, *, tool_exited: bool = False) -> BudgetState:
        if requests is not None:
            # Monotonic on purpose: a transient collector error must not let the
            # counter go backwards and hand the tool extra budget.
            self.requests = max(self.requests, int(requests))
        reason: StopReason | None = None
        if tool_exited:
            reason = StopReason.COMPLETED
        elif self.budget.wall_clock_s is not None and self.elapsed >= self.budget.wall_clock_s:
            reason = StopReason.BUDGET_WALL_CLOCK
        elif self.budget.max_requests is not None and self.requests >= self.budget.max_requests:
            reason = StopReason.BUDGET_REQUESTS
        return BudgetState(elapsed_s=self.elapsed, requests=self.requests, reason=reason)
