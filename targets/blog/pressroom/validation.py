"""One validation and error style for the whole API.

Every endpoint reads its inputs through these helpers, so a caller gets the same shape
of refusal whichever endpoint refused them, and a reviewer can tell at a glance which
inputs an endpoint actually looked at.
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import HTTPException

SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,80}$")
IDENT = re.compile(r"^[a-z]{3}-[a-z0-9-]{1,32}$")
HANDLE = re.compile(r"^[a-z][a-z-]{1,40}$")
EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{3,190}$")


def bad(message: str) -> HTTPException:
    return HTTPException(status_code=400, detail=message)


def text(value: Any, field: str, *, maximum: int = 400, minimum: int = 1) -> str:
    if not isinstance(value, str):
        raise bad(f"`{field}` must be text.")
    value = value.strip()
    if len(value) < minimum:
        raise bad(f"`{field}` is required.")
    if len(value) > maximum:
        raise bad(f"`{field}` is limited to {maximum} characters.")
    return value


def number(value: Any, field: str, *, low: int, high: int, fallback: int | None = None) -> int:
    if value is None and fallback is not None:
        return fallback
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise bad(f"`{field}` must be a whole number.") from None
    if not low <= parsed <= high:
        raise bad(f"`{field}` must be between {low} and {high}.")
    return parsed


def slug(value: Any, field: str = "slug") -> str:
    candidate = text(value, field, maximum=90)
    if not SLUG.match(candidate):
        raise bad(f"`{field}` is not a valid slug.")
    return candidate


def identifier(value: Any, field: str) -> str:
    candidate = text(value, field, maximum=40)
    if not IDENT.match(candidate):
        raise bad(f"`{field}` is not a valid identifier.")
    return candidate


def handle(value: Any, field: str = "handle") -> str:
    candidate = text(value, field, maximum=42)
    if not HANDLE.match(candidate):
        raise bad(f"`{field}` is not a valid handle.")
    return candidate


def one_of(value: Any, field: str, allowed: tuple[str, ...], fallback: str | None = None) -> str:
    if value is None and fallback is not None:
        return fallback
    if value not in allowed:
        raise bad(f"`{field}` must be one of {', '.join(allowed)}.")
    return str(value)


def missing(what: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"No {what} with that identifier.")
