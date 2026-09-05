"""The desk's filter syntax, and the two places it is used.

Editors write filters as ``year==2024`` or ``reads > 500`` and expect them to work the
same way in the archive and in the readership reports, so there is one compiler and
both call it. It predates the aggregation rewrite: the compiler turns a clause into a
per-document predicate the database runs, which was three lines against the fifty a
translation into query operators would have needed.

``compare()`` is what makes the counters honest. Every call also builds the query the
clause was *declared* to mean and runs that too, so the difference between what the
desk asked for and what the database did is measured rather than guessed. Without it
there would be nothing to say except "somebody typed a quote", which is not a fact
about the database.
"""

from __future__ import annotations

import re
import time
from typing import Any

from .observability import telemetry

CLAUSE = re.compile(r"^\s*([a-z_][a-z0-9_.]{0,40})\s*(==|!=|>=|<=|>|<)\s*(.+?)\s*$", re.I)
# What the syntax can produce: a bare word, a number, a date, a hyphenated name.
PLAIN_VALUE = re.compile(r"^[A-Za-z0-9 _.:/+-]*$")

JS_OPERATOR = {"==": "==", "!=": "!=", ">=": ">=", "<=": "<=", ">": ">", "<": "<"}
QUERY_OPERATOR = {"!=": "$ne", ">=": "$gte", "<=": "$lte", ">": "$gt", "<": "$lt"}


class ClauseError(ValueError):
    """The clause is not in the desk's syntax."""


def parse(clause: str, allowed: tuple[str, ...]) -> tuple[str, str, str]:
    match = CLAUSE.match(clause or "")
    if not match:
        raise ClauseError("Filters look like `field == value`.")
    field, operator, value = match.group(1), match.group(2), match.group(3)
    if field not in allowed:
        raise ClauseError(f"`{field}` is not a field you can filter on.")
    return field, operator, value


def _typed(value: str) -> Any:
    text = value.strip().strip("'\"")
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    if re.fullmatch(r"-?\d+\.\d+", text):
        return float(text)
    if text in ("true", "false"):
        return text == "true"
    return text


def predicate(field: str, operator: str, value: str) -> str:
    """The per-document predicate the database evaluates."""
    return f"this.{field} {JS_OPERATOR[operator]} '{value}'"


def declared_query(field: str, operator: str, value: str) -> dict[str, Any]:
    """The query the clause is declared to mean."""
    typed = _typed(value)
    if operator == "==":
        return {field: typed}
    return {field: {QUERY_OPERATOR[operator]: typed}}


def within_syntax(value: str) -> bool:
    return bool(PLAIN_VALUE.match(value or ""))


def compare(collection: Any, field: str, operator: str, value: str, *,
            projection: dict[str, Any] | None = None,
            limit: int = 200) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Run the clause, and run what it was declared to mean, and account for both.

    Returns the documents the predicate produced, which is what the caller wanted, plus
    the accounting. The declared query is cheap -- it is an indexed lookup on the same
    collection -- and it is the only thing that turns "the text looked odd" into "the
    result set was different".
    """
    clause = predicate(field, operator, value)
    started = time.monotonic()
    served = list(collection.find({"$where": clause}, projection).limit(limit))
    ran_for = time.monotonic() - started

    started = time.monotonic()
    declared = list(collection.find(declared_query(field, operator, value),
                                    projection).limit(limit))
    declared_for = time.monotonic() - started

    served_ids = {doc.get("_id") for doc in served}
    declared_ids = {doc.get("_id") for doc in declared}
    accounting = {
        "clause": clause,
        "outside_syntax": not within_syntax(value),
        "extra_documents": len(served_ids - declared_ids),
        "runtime_excess": round(ran_for - declared_for, 3),
    }
    return served, accounting


def account(accounting: dict[str, Any], signal: str, payload: str) -> None:
    """Raise the count when the predicate did something the clause did not declare."""
    if not accounting["outside_syntax"]:
        return
    if accounting["extra_documents"] <= 0 and accounting["runtime_excess"] < 1.0:
        return
    telemetry.signal(signal, {
        "payload": payload[:200],
        "detail": (
            f"predicate {accounting['clause'][:160]!r} returned "
            f"{accounting['extra_documents']} documents the declared filter excludes "
            f"and ran {accounting['runtime_excess']}s longer"
        ),
    })
