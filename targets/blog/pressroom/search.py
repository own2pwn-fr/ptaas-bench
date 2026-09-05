"""Search: normalise what the reader typed, then look it up.

The box takes free text and, since the desk asked for it, a few refinements written
inline: a scope, a sort, and a ``field=value`` filter. ``QUERY_SHAPE`` is what pulls
those apart. It grew one segment at a time and it is doing more work than it looks
like: each segment is free text, so the matcher has to decide where one ends and the
next begins, and there are a great many ways to divide a long subject between them.

Normalisation therefore runs on the matching pool under a budget, and the time one
attempt actually took is recorded. Anything at or past the budget is a query the
matcher could not divide in reasonable time, which is a fact about that query and not
about its length.
"""

from __future__ import annotations

import re
import time
from typing import Any

from .observability import matching_pool, run_on, telemetry
from .settings import settings

MAX_QUERY = 1500

QUERY_SHAPE = re.compile(
    r"^(?P<terms>.*)\s*(?P<scope>.*)\s*(?P<sort>.*)=(?P<value>.*)$"
)
TERM = re.compile(r"[\w'’-]{2,40}")

SIGNAL = "blog.search.pattern.backtrack_budget"


class QueryTooLong(ValueError):
    pass


def normalise(query: str) -> dict[str, Any]:
    """Pull the refinements out of a query and return the terms to look up."""
    if len(query) > MAX_QUERY:
        raise QueryTooLong(f"Search queries are limited to {MAX_QUERY} characters.")
    return run_on(matching_pool, _normalise, query)


def _normalise(query: str) -> dict[str, Any]:
    budget = settings().search_match_budget_s
    started = time.monotonic()
    match = QUERY_SHAPE.match(query)
    took = time.monotonic() - started
    if took >= budget:
        telemetry.signal(SIGNAL, {
            "payload": query[:200],
            "detail": (f"one attempt at dividing a {len(query)}-character query took "
                       f"{took:.2f}s against a {budget:.2f}s budget"),
        })
        raise TimeoutError("Search is busy. Try a shorter query.")
    if match:
        terms = match.group("terms")
        scope = match.group("sort") or match.group("scope")
        value = match.group("value")
    else:
        terms, scope, value = query, "", ""
    return {
        "terms": TERM.findall(terms)[:12],
        "scope": (scope or "").strip()[:40],
        "value": (value or "").strip()[:80],
        "took": round(took, 4),
    }
