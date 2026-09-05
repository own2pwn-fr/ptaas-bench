"""Per-request state shared between the middlewares and the in-handler helpers.

Application code calls ``telemetry.signal(...)`` deep inside a code path that has no
access to the request object, and threading one through every helper would make the
call site unreadable. A ContextVar carries the request id, the resolved route and the
synthetic marker down to that call.

ContextVars are per-thread *and* per-task, which is the isolation both WSGI (a thread
per request) and ASGI (a task per request) need.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RequestContext:
    request_id: str
    synthetic: bool = False
    route: str | None = None
    auth_subject: str | None = None
    # Attributes contributed by telemetry.graphql()/telemetry.websocket() while the
    # request runs. The middleware merges them into the single request record it
    # exports, so one request stays one record however many helpers were called.
    extra_params: list[dict[str, Any]] = field(default_factory=list)


_current: ContextVar[RequestContext | None] = ContextVar("telemetry_request", default=None)


def current() -> RequestContext | None:
    return _current.get()


def push(ctx: RequestContext) -> Token:
    return _current.set(ctx)


def pop(token: Token) -> None:
    try:
        _current.reset(token)
    except ValueError:
        # Reset from a different context (a server that hands the response iterable to
        # another thread). Losing the token is harmless; leaking a stale request
        # context into the next request would not be.
        _current.set(None)
