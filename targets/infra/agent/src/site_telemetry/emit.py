"""Turn what the servers recorded into records of our own.

The estate's telemetry library was written for services that call it from inside their
own request handler, where the peer, the route and the request identity are already on
the call stack. Nothing on this estate works that way: the web tier is a file server and
the datastores are datastores, so the agent reads what each of them writes about a
request that has already finished, and has to put the same three things back before it
records anything. That is what :func:`observed` does -- it is the request context for a
server that is not in this process.

The peer is always the address the server itself saw on the socket. Nothing a client can
say about itself is ever used for it: a forwarded header is written by the client, and
here the client is whoever is looking at us.
"""

from __future__ import annotations

import time
import uuid
from contextlib import contextmanager
from typing import Any, Iterator, Mapping

from telemetry_agent import _context, get_telemetry, init_telemetry

__all__ = ["start", "observed", "request_id", "record_request", "signal", "client"]


def start(**overrides: Any):
    return init_telemetry(**overrides)


def client():
    return get_telemetry()


def request_id() -> str:
    return uuid.uuid4().hex


@contextmanager
def observed(peer: str, route: str | None = None, identifier: str | None = None,
             synthetic: bool | None = None) -> Iterator[str]:
    """Run a block as though it were the handler of the request just observed."""
    telemetry = get_telemetry()
    identifier = identifier or request_id()
    if synthetic is None:
        synthetic = telemetry.is_synthetic_peer(peer)
    token = _context.push(_context.RequestContext(
        request_id=identifier,
        synthetic=bool(synthetic),
        peer_ip=peer or "",
        client_ip=peer or "",
        route=route,
    ))
    try:
        yield identifier
    finally:
        _context.pop(token)


def record_request(*, method: str, route: str, path: str, status: int | None,
                   peer: str, params=(), user_agent: str = "",
                   identifier: str | None = None, host: str | None = None) -> None:
    """Export one request record.

    Built here rather than handed to ``TelemetryClient.record_request`` for one reason:
    this estate serves several sites from one server, so a record has to say which of
    them answered, and an in-process middleware -- which is what that method was written
    for -- never has to, because a process is one site. The shape is otherwise the
    library's own, and a test holds the two together.
    """
    telemetry = get_telemetry()
    record = {
        "type": "http_request",
        "app": telemetry.config.service,
        "ts": time.time(),
        "synthetic": telemetry.is_synthetic_peer(peer),
        "peer_ip": peer,
        "method": method,
        "route": route,
        "path": path,
        "status": status,
        "auth_subject": None,
        "client_ip": peer,
        "user_agent": user_agent or "",
        "params": list(params),
        "request_id": identifier or request_id(),
    }
    # Absent rather than guessed: a name none of the sites claims is served by the first
    # one, which is a fact about the configuration and not about the visitor.
    if host:
        record["host"] = host
    telemetry.emit(record)


def signal(name: str, attributes: Mapping[str, Any], *, peer: str,
           route: str | None = None, identifier: str | None = None) -> None:
    with observed(peer, route, identifier):
        get_telemetry().signal(name, dict(attributes))
