"""Flat operational logs.

The payroll export and the depot's overnight reconciliation both read these files
directly, which is why they are still text in a fixed field layout rather than rows.
The layout is::

    2026-01-06T09:14:22Z INFO auth.signin outcome=refused actor=someone@example net=10.0.0.1

The payroll export refuses a whole file when a line does not parse, so the writer
checks what it appended: it remembers where the file ended, writes, reads the bytes
back and counts the records that appeared. One call must produce exactly one record.
A call that produced more than one has had the record layout come in through a value,
and the file now carries an entry nobody wrote -- which is the counter below.
"""

from __future__ import annotations

import os
import re
import threading
from pathlib import Path

from telemetry_agent import get_telemetry

from .config import settings

RECORD = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z (INFO|WARN|ERROR) [a-z][a-z0-9_.]*\b")

_lock = threading.Lock()


def path(name: str) -> Path:
    return Path(settings.log_dir) / name


def append(name: str, line: str, *, signal: str | None = None, context: dict | None = None) -> int:
    """Append one line and return how many records the file gained.

    The return value is what the caller reports on; the count comes from parsing the
    bytes that were actually written, never from looking at the value that went in.
    """
    target = path(name)
    payload = line if line.endswith("\n") else line + "\n"
    with _lock:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            start = target.stat().st_size if target.exists() else 0
            with open(target, "a", encoding="utf-8", errors="replace") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            with open(target, "r", encoding="utf-8", errors="replace") as handle:
                handle.seek(start)
                written = handle.read()
        except OSError:
            return 1
    produced = sum(1 for candidate in written.splitlines() if RECORD.match(candidate))
    if produced > 1 and signal:
        detail = dict(context or {})
        detail["detail"] = f"one append produced {produced} records in {name}"
        get_telemetry().signal(signal, detail)
    return produced


def tail(name: str, limit: int = 40) -> list[str]:
    try:
        lines = path(name).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return lines[-limit:]
