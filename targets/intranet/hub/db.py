"""SQLite access.

One connection per request, opened lazily and closed when the request ends. The file
is small and the readership is a few hundred people, so a connection pool would be
ceremony; what matters is that a rebuild of the file by the deployment tooling is
picked up without a restart, which per-request connections give for free.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Iterable

from flask import g

from .config import settings


def connect(path: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or settings.database_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def handle() -> sqlite3.Connection:
    conn = getattr(g, "_hub_db", None)
    if conn is None:
        conn = connect()
        g._hub_db = conn
    return conn


def close(_exc: BaseException | None = None) -> None:
    conn = getattr(g, "_hub_db", None)
    if conn is not None:
        g._hub_db = None
        try:
            conn.close()
        except sqlite3.Error:
            pass


def rows(sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
    return handle().execute(sql, tuple(params)).fetchall()


def row(sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
    return handle().execute(sql, tuple(params)).fetchone()


def value(sql: str, params: Iterable[Any] = (), default: Any = None) -> Any:
    found = row(sql, params)
    return found[0] if found is not None else default


def write(sql: str, params: Iterable[Any] = ()) -> int:
    conn = handle()
    cur = conn.execute(sql, tuple(params))
    conn.commit()
    return cur.lastrowid


def seen_once(key: str) -> bool:
    """True the first time a counter key is offered, false afterwards.

    The anomaly counters in this application are de-duplicated so that one bad row
    does not flood a dashboard with the same occurrence every time a page is drawn.
    The keys live in the database rather than in memory because there is more than
    one worker and the deployment tooling rebuilds the file between releases.
    """
    conn = handle()
    try:
        conn.execute("INSERT INTO counter_keys (key, at) VALUES (?, datetime('now'))", (key,))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
