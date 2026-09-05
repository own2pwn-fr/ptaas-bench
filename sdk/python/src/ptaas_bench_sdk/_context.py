"""Per-request state shared between the middlewares and the in-handler helpers.

A planted sink calls ``bench.trigger(...)`` deep inside the application; it has no
access to the request object and must not be forced to thread one through, because the
call has to stay a one-liner that a reader can grep for. A ContextVar carries the
request id and the synthetic flag from the middleware down to that call.

ContextVars are per-thread *and* per-task, which is exactly the isolation both WSGI
(thread per request) and ASGI (task per request) need.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RequestContext:
    request_id: str
    synthetic: bool = False
    auth_subject: str | None = None
    # Entries contributed by bench.graphql()/bench.websocket() during the request; the
    # middleware merges them into the single http_request event it emits, so one
    # request stays one event no matter how many helpers the handler calls.
    extra_params: list[dict[str, Any]] = field(default_factory=list)


_current: ContextVar[RequestContext | None] = ContextVar("ptaas_bench_request", default=None)


def current() -> RequestContext | None:
    return _current.get()


def push(ctx: RequestContext) -> Token:
    return _current.set(ctx)


def pop(token: Token) -> None:
    try:
        _current.reset(token)
    except ValueError:
        # Reset from a different context (e.g. a WSGI server handing the response
        # iterable to another thread). Losing the token is harmless; leaking a stale
        # request context into the next request would not be.
        _current.set(None)
