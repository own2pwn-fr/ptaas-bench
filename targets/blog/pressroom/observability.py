"""Telemetry wiring, worker pools and the runtime integrity monitor.

Three things live here because all three are about knowing what the process did while
it was serving somebody, and all three have to stay off the response path.

**The agent.** ``telemetry_agent`` is the estate's observability library. One request
record per request, plus named counters the application raises when it notices
something worth counting. It is fire-and-forget: a collector that is down changes
nothing observable in this service, including its response times.

**The pools.** Image conversion and query normalisation are blocking work and run on
their own pools rather than on the event loop. A pool worker that ran in a bare thread
would lose the request context the agent keeps in a ``ContextVar``: ``ThreadPoolExecutor``
copies nothing, so the counters a worker raises would arrive detached from the request
that caused them, and every dashboard that joins the two would show a gap. ``run_on()``
therefore copies the calling context explicitly and runs the work inside it.

**The integrity monitor.** The conversion pool reconstructs tone curves out of files
the picture desk hands it. An interpreter-level audit hook records when work inside a
watched section reaches for the interpreter itself -- an import, a file, a process, a
socket -- which is the one thing curve arithmetic never needs. The hook is installed
once, does nothing but read a thread-local on every event, and is inert outside a
watched section.
"""

from __future__ import annotations

import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, TypeVar

from telemetry_agent import init_telemetry

telemetry = init_telemetry()

T = TypeVar("T")

# Conversion is CPU-bound and the pool is small on purpose: a burst of adjustments
# should queue rather than take the machine away from everything else.
conversion_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="convert")
# Query normalisation is short but not free, and it runs under a budget.
matching_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="match")


def run_on(pool: ThreadPoolExecutor, fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Run blocking work on ``pool`` inside the caller's context.

    A pool hands the callable to a thread it made long before this request existed, and
    it copies nothing across, so the work would start with an empty context and
    anything it recorded would be attributed to no request at all. ``telemetry.bind``
    is the agent's own seam for that: the wrapped callable re-enters the caller's
    context, so the peer, the request id and the classification of the traffic all
    survive the hop.
    """
    return pool.submit(telemetry.bind(fn), *args, **kwargs).result()


# --------------------------------------------------------------------- integrity

_local = threading.local()

# Event-name prefixes that arithmetic never needs. `compile` and `exec` are
# left out on purpose: a curve is reconstructed by compiling an expression, so counting
# those would count ordinary work on every well-formed file.
_WATCHED = (
    "import",
    "open",
    "os.",
    "subprocess.",
    "socket.",
    "shutil.",
    "tempfile.",
    "urllib.",
    "http.client.",
    "ftplib.",
    "smtplib.",
    "ctypes.",
    "pty.",
    "glob.",
    "pickle.",
    "marshal.",
)


class _Watch:
    """Marks the section of work an integrity observation belongs to."""

    def __init__(self, label: str) -> None:
        self.label = label
        self.observed: list[str] = []
        self._previous: Any = None

    def __enter__(self) -> "_Watch":
        self._previous = getattr(_local, "watch", None)
        _local.watch = self
        return self

    def __exit__(self, *exc: Any) -> None:
        _local.watch = self._previous


def watch(label: str) -> _Watch:
    """Watch the current thread for interpreter-level activity while inside the block."""
    return _Watch(label)


def _audit(event: str, _args: tuple) -> None:
    current = getattr(_local, "watch", None)
    if current is None:
        return
    for prefix in _WATCHED:
        if event == prefix or event.startswith(prefix):
            if len(current.observed) < 8 and event not in current.observed:
                current.observed.append(event)
            return


def install_integrity_monitor() -> None:
    """Install the audit hook once per process. Idempotent, and never removable."""
    if getattr(install_integrity_monitor, "_done", False):
        return
    sys.addaudithook(_audit)
    install_integrity_monitor._done = True  # type: ignore[attr-defined]


install_integrity_monitor()
