"""ASGI middleware for Starlette and FastAPI services.

Records the **route template as registered** (``/api/orders/{id}``), never the concrete
URL. Concrete paths would give every identifier its own time series and make
per-endpoint latency, error rate and throughput unusable within a day; templates keep
cardinality bounded and comparable across deploys. The concrete path is still kept on
the record, where it costs nothing.

Starlette does not publish the matched route in the scope (``Route.matches`` exports
only ``endpoint`` and ``path_params``, and that holds across the versions supported
here), so the route tree is walked with the framework's own ``matches()`` predicate.
Walking it also yields the mount prefix, which is the only way to get
``/sub/items/{item_id}`` instead of a sub-application's local ``/items/{item_id}``.

Usage::

    app.add_middleware(TelemetryASGIMiddleware, framework_app=app)
    app = TelemetryASGIMiddleware(app)     # or wrap directly
"""

from __future__ import annotations

import uuid
from typing import Any, Callable, Iterable, Sequence

from . import _context
from ._client import TelemetryClient, get_telemetry, peer_matches_forwarded_claim
from ._params import collect_body, collect_headers, collect_query

UNMATCHED = "<unmatched>"

# Mount compiles its path as "<prefix>/{path:path}"; the trailing catch-all belongs to
# the mount's implementation, not to any registered template.
_MOUNT_SUFFIX = "/{path}"


def _decode_headers(raw: Iterable[tuple[bytes, bytes]]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for key, value in raw:
        try:
            out.append((key.decode("latin-1").lower(), value.decode("latin-1")))
        except Exception:  # noqa: BLE001
            continue
    return out


def _match_value(match: Any) -> int:
    # Starlette's Match is a plain Enum (NONE/PARTIAL/FULL = 0/1/2). Reading .value
    # avoids importing starlette, so this module stays usable in a WSGI-only service.
    return int(getattr(match, "value", match) or 0)


def resolve_route(routes: Sequence[Any], scope: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
    """Return ``(template, path_params)`` for the first route matching ``scope``.

    Mirrors ``Router.app``: a full match wins immediately, while a route that matches
    the path but not the method (PARTIAL, what Starlette answers 405 to) is kept as a
    fallback -- a 405 belongs to that endpoint's time series, not to a nameless bucket.
    """
    partial: tuple[str, dict[str, Any]] | None = None
    for route in routes or ():
        try:
            match, child = route.matches(scope)
        except Exception:  # noqa: BLE001
            continue
        rank = _match_value(match)
        if rank == 0:
            continue
        path_format = getattr(route, "path_format", None)
        sub_routes = getattr(route, "routes", None)
        if sub_routes:  # Mount / Host: descend, carrying the prefix
            prefix = path_format or ""
            if prefix.endswith(_MOUNT_SUFFIX):
                prefix = prefix[: -len(_MOUNT_SUFFIX)]
            sub_scope = {**scope, **child}
            template, params = resolve_route(sub_routes, sub_scope)
            if template is not None:
                merged = {**child.get("path_params", {}), **params}
                merged.pop("path", None)  # the mount's catch-all, not a real variable
                return (prefix + template, merged)
            continue
        if path_format is None:
            continue
        found = (path_format, dict(child.get("path_params", {})))
        if rank >= 2:
            return found
        if partial is None:
            partial = found
    return partial if partial is not None else (None, {})


class TelemetryASGIMiddleware:
    """Export exactly one request record per request, after the response is sent."""

    def __init__(
        self,
        app: Any,
        *,
        telemetry: TelemetryClient | None = None,
        framework_app: Any = None,
        routes: Sequence[Any] | Callable[[], Sequence[Any]] | None = None,
        auth_subject: Callable[[dict[str, Any]], str | None] | None = None,
    ) -> None:
        self.app = app
        self._telemetry = telemetry
        self._framework_app = framework_app
        self._routes = routes
        self._auth_subject = auth_subject
        self._routes_holder: Any = None

    @property
    def telemetry(self) -> TelemetryClient:
        return self._telemetry or get_telemetry()

    # ------------------------------------------------------------------- routing

    def _route_list(self) -> Sequence[Any]:
        """Re-read the route table on each request: routers can still be including
        routes after the middleware object was built."""
        if callable(self._routes):
            try:
                return self._routes() or ()
            except Exception:  # noqa: BLE001
                return ()
        if self._routes is not None:
            return self._routes
        holder = self._routes_holder
        if holder is not None:
            return getattr(holder, "routes", ()) or ()
        for candidate in (self._framework_app, self.app):
            holder = _find_routes_holder(candidate)
            if holder is not None:
                self._routes_holder = holder
                return getattr(holder, "routes", ()) or ()
        return ()

    def _template(self, scope: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        try:
            template, params = resolve_route(self._route_list(), scope)
            if template:
                return template, params
            # Fallback for routers that do publish the match in the scope.
            route = scope.get("route")
            path_format = getattr(route, "path_format", None) or getattr(route, "path", None)
            if isinstance(path_format, str) and path_format:
                return path_format, dict(scope.get("path_params") or {})
        except Exception:  # noqa: BLE001
            pass
        return UNMATCHED, {}

    # -------------------------------------------------------------------- ASGI

    async def __call__(self, scope: dict[str, Any], receive: Callable, send: Callable) -> None:
        if scope.get("type") not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        try:
            headers = _decode_headers(scope.get("headers") or ())
            header_map = {name: value for name, value in headers}
            # ASGI's scope["client"] is the socket peer. No forwarded header is ever
            # consulted here: a caller that could set its own classification could
            # remove its traffic from every dashboard it appears in. An address the
            # caller also announced in a forwarded header is a claim, not a socket, so
            # it neither classifies here nor travels on the record as peer_ip.
            client = scope.get("client") or ()
            client_ip = client[0] if client else ""
            peer_ip = "" if peer_matches_forwarded_claim(client_ip, header_map) else client_ip
            synthetic = self.telemetry.is_synthetic_peer(peer_ip)
            # The route is resolved up front so handlers can name it while they run.
            # Matching sees a snapshot: the router rewrites root_path and path_params
            # in place, and the record must describe the request as it arrived.
            scope_snapshot = dict(scope)
            template, path_params = self._template(scope_snapshot)
            ctx = _context.RequestContext(
                request_id=uuid.uuid4().hex,
                synthetic=synthetic,
                peer_ip=peer_ip,
                client_ip=client_ip,
                route=template,
            )
            token = _context.push(ctx)
        except Exception:  # noqa: BLE001
            await self.app(scope, receive, send)
            return

        status: dict[str, int | None] = {"code": None}
        body_recorder: _BodyRecorder | None = None

        async def send_wrapper(message: dict[str, Any]) -> None:
            if message.get("type") in ("http.response.start", "websocket.accept"):
                status["code"] = message.get("status", 101 if message["type"] == "websocket.accept" else None)
            await send(message)

        receive_wrapper = receive
        if scope.get("type") == "http" and _declares_body(header_map):
            body_recorder = _BodyRecorder(receive, self.telemetry.config.max_body_bytes)
            await body_recorder.prefetch()
            receive_wrapper = body_recorder.receive

        try:
            await self.app(scope, receive_wrapper, send_wrapper)
        finally:
            # Runs once the last response byte has been handed to the server, so the
            # record is never assembled between the application and the client.
            try:
                self._record(
                    scope_snapshot, scope, headers, header_map, ctx,
                    template, path_params, client_ip, status["code"], body_recorder,
                )
            except Exception:  # noqa: BLE001
                pass
            _context.pop(token)

    def _record(
        self,
        scope_snapshot: dict[str, Any],
        scope: dict[str, Any],
        headers: list[tuple[str, str]],
        header_map: dict[str, str],
        ctx: _context.RequestContext,
        template: str,
        path_params: dict[str, Any],
        client_ip: str,
        status: int | None,
        body_recorder: "_BodyRecorder | None",
    ) -> None:
        telemetry = self.telemetry
        if not telemetry.config.enabled:
            return
        if not path_params:
            # The router fills these in even when our own match came up short (custom
            # Route subclasses, third-party routers).
            path_params = dict(scope.get("path_params") or {})

        collector = telemetry.new_param_collector()
        collect_query(collector, scope_snapshot.get("query_string", b""))
        collector.add_many(((str(k), _stringify(v)) for k, v in path_params.items()), "path")
        collect_headers(collector, headers)
        if body_recorder is not None:
            collect_body(collector, body_recorder.body, header_map.get("content-type", ""))
        collector.extend_entries(ctx.extra_params)

        auth_subject = ctx.auth_subject
        if auth_subject is None and self._auth_subject is not None:
            try:
                auth_subject = self._auth_subject(scope)
            except Exception:  # noqa: BLE001
                auth_subject = None

        method = scope_snapshot.get("method") or ("WEBSOCKET" if scope_snapshot.get("type") == "websocket" else "GET")
        telemetry.record_request(
            method=method,
            route=ctx.route or template,
            path=scope_snapshot.get("path", ""),
            status=status,
            params=collector.entries,
            auth_subject=auth_subject,
            client_ip=client_ip,
            user_agent=header_map.get("user-agent", ""),
            request_id=ctx.request_id,
            synthetic=ctx.synthetic,
            peer_ip=ctx.peer_ip,
        )


def _stringify(value: Any) -> str:
    return value if isinstance(value, str) else str(value)


def _declares_body(header_map: dict[str, str]) -> bool:
    """Only touch ``receive`` when the request actually announces a body.

    Reading a body that was never sent would wait until the client gave up, which is
    the one way this middleware could add real latency.
    """
    if "chunked" in header_map.get("transfer-encoding", "").lower():
        return True
    try:
        return int(header_map.get("content-length", "0")) > 0
    except ValueError:
        return False


class _BodyRecorder:
    """Buffer the request body for attribute extraction, then replay it to the app.

    The application must receive its body byte for byte: buffered messages are replayed
    in order, and anything past ``max_bytes`` streams straight from the original
    ``receive``, so a large upload is neither held in memory nor delayed.
    """

    def __init__(self, receive: Callable, max_bytes: int) -> None:
        self._receive = receive
        self._max = max_bytes
        self._buffered: list[dict[str, Any]] = []
        self.body = b""
        self.truncated = False

    async def prefetch(self) -> None:
        chunks: list[bytes] = []
        total = 0
        while True:
            message = await self._receive()
            self._buffered.append(message)
            if message.get("type") != "http.request":
                break  # a disconnect is replayed too: the app must still see it
            chunk = message.get("body", b"") or b""
            if total < self._max:
                chunks.append(chunk[: self._max - total])
            total += len(chunk)
            if not message.get("more_body", False):
                break
            if total >= self._max:
                self.truncated = True
                break
        self.body = b"".join(chunks)

    async def receive(self) -> dict[str, Any]:
        if self._buffered:
            return self._buffered.pop(0)
        return await self._receive()


def _find_routes_holder(node: Any, max_depth: int = 12) -> Any:
    """Find the object owning the route table by following the ASGI wrapper chain.

    ``app.add_middleware(TelemetryASGIMiddleware)`` passes the *inner* middleware, not
    the application, so the router has to be found through ``.app`` / ``.router``.
    """
    for _ in range(max_depth):
        if node is None:
            return None
        routes = getattr(node, "routes", None)
        if isinstance(routes, (list, tuple)):
            return node
        node = getattr(node, "app", None) or getattr(node, "router", None)
    return None
