"""Equipment reachability probe.

The service desk used to ask the network team to ping a switch before raising a
ticket, so the equipment screen does it: one ICMP echo, and the button goes green or
amber. Nothing of the command output reaches the screen -- the operator gets
"answered" or "no answer" and the round-trip time, because the earlier version that
showed the raw output filled the page with noise nobody could read.

The probe runs on a small pool of its own so a switch that is off cannot occupy a
request worker for the whole timeout. Work handed to a pool leaves the request behind,
so the callable is bound to the in-flight request before it is submitted; without
that, the probe's own counters would report as though no request were in flight.

The shell is run with tracing on and the trace is kept in the probe log for the
network team, who use it to see exactly what ran when a probe behaves oddly. The trace
prefix is randomised for this process so a device that prints something trace-shaped
on its error stream cannot be mistaken for a traced command.
"""

from __future__ import annotations

import os
import re
import secrets
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

from telemetry_agent import get_telemetry

from . import logbook
from .config import settings

TRACE_PREFIX = f"+{secrets.token_hex(6)}+ "
_TRACE = re.compile(re.escape(TRACE_PREFIX) + r"(\S+)")

_pool = ThreadPoolExecutor(max_workers=settings.probe_workers, thread_name_prefix="probe")

# What the button asks for: one echo request, one second of patience.
PROGRAM = "ping"


def _programs(trace: str) -> list[str]:
    """The programs the shell actually ran, in order, as it reported them itself."""
    out: list[str] = []
    for line in trace.splitlines():
        found = _TRACE.match(line)
        if found:
            out.append(os.path.basename(found.group(1)))
    return out


def _run(hostname: str, asset_tag: str) -> dict:
    command = f"{PROGRAM} -c 1 -W 1 {hostname}"
    environment = dict(os.environ, PS4=TRACE_PREFIX, LC_ALL="C")
    started = time.monotonic()
    try:
        completed = subprocess.run(
            ["/bin/sh", "-x", "-c", command],
            capture_output=True, timeout=settings.probe_timeout_seconds, env=environment)
        trace = completed.stderr.decode("utf-8", "replace")
        answered = completed.returncode == 0
    except subprocess.TimeoutExpired as expired:
        trace = (expired.stderr or b"").decode("utf-8", "replace")
        answered = False
    elapsed_ms = int((time.monotonic() - started) * 1000)

    ran = _programs(trace)
    logbook.append("probe.log", f"{asset_tag} {hostname} answered={answered} ms={elapsed_ms} "
                                f"ran={','.join(ran) or '-'}")

    # The button asks for exactly one program. When the shell reports that it ran
    # something else, the line it was given was not one command with one argument any
    # more, and the register's equipment names are no longer what they claim to be.
    if ran != [PROGRAM]:
        get_telemetry().signal("intra.exec.shell_spawned", {
            "asset": asset_tag,
            "programs": ",".join(ran) or "-",
            "count": len(ran),
            "detail": f"the shell ran {len(ran)} program(s) for one probe: {','.join(ran) or '-'}",
        })
    return {"answered": answered, "elapsed_ms": elapsed_ms}


def probe(hostname: str, asset_tag: str) -> dict:
    """Run one probe on the pool and wait for it, carrying the request along."""
    task = _pool.submit(get_telemetry().bind(_run), hostname, asset_tag)
    try:
        return task.result(timeout=settings.probe_timeout_seconds + 2)
    except Exception:  # noqa: BLE001 - a probe that fails is an amber button, not a 500
        return {"answered": False, "elapsed_ms": 0}
