"""ASGI middleware for Starlette / FastAPI targets.

Reports the **route template as registered** (``/api/orders/{id}``), never the concrete
URL. The catalog writes its entrypoints against templates, so a tool that hits
``/api/orders/42`` must credit reach on ``/api/orders/{id}``; if the SDK reported
concrete paths, every fuzzed id would look like a different, unknown endpoint and reach
would never be scored.

The template is reported exactly as Starlette registered it; normalising the three
spellings the platform sees (``{id}`` here, ``<int:id>`` in Flask, ``:id`` in the
catalog) is the scorer's job, since it is the only component that reads the catalog and
can therefore be the only place that mapping is defined.

Starlette does not put the matched route in the scope (``Route.matches`` only exports
``endpoint`` and ``path_params``, and that has been true across the versions this has
to support), so the route tree is walked here with the framework's own ``matches()``
predicate. Walking it ourselves also yields the mount prefix, which is the only way to
get ``/sub/items/{item_id}`` rather than the sub-app's local ``/items/{item_id}``.

Usage::

    app.add_middleware(BenchASGIMiddleware, framework_app=app)   # FastAPI/Starlette
    app = BenchASGIMiddleware(app)                               # or wrap directly
"""

from __future__ import annotations

import uuid
from typing import Any, Callable, Iterable, Sequence

from . import _context
from ._client import BenchClient, get_bench
from ._params import collect_body, collect_headers, collect_query

UNMATCHED = "<unmatched>"

# Mount compiles its path as "<prefix>/{path:path}"; the trailing catch-all is an
# implementation detail of the mount, not part of any registered template.
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
    # Starlette's Match is a plain Enum (NONE/PARTIAL/FULL = 0/1/2). Read .value so the
    # SDK never has to import starlette, which keeps it usable in Flask-only targets.
    return int(getattr(match, "value", match) or 0)


def resolve_route(routes: Sequence[Any], scope: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
    """Return ``(template, path_params)`` for the first route matching ``scope``.

    Mirrors ``Router.app``: a full match wins immediately, an otherwise-matching route
    with the wrong method (PARTIAL, what Starlette answers 405 to) is kept as a
    fallback, because a 405 still proves the tool reached that endpoint.
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
                merged.pop("path", None)  # the mount's catch-all, not a real parameter
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


class BenchASGIMiddleware:
    """Emit exactly one ``http_request`` event per request, after the response is sent."""

    def __init__(
        self,
        app: Any,
        *,
        bench: BenchClient | None = None,
        framework_app: Any = None,
        routes: Sequence[Any] | Callable[[], Sequence[Any]] | None = None,
        auth_subject: Callable[[dict[str, Any]], str | None] | None = None,
    ) -> None:
        self.app = app
        self._bench = bench
        self._framework_app = framework_app
        self._routes = routes
        self._auth_subject = auth_subject
        self._routes_holder: Any = None

    @property
    def bench(self) -> BenchClient:
        return self._bench or get_bench()

    # ------------------------------------------------------------------- routing

    def _route_list(self) -> Sequence[Any]:
        """Re-read the route table every request: FastAPI routers can still be
        including routes after the middleware object was built."""
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
            # Fallback for ASGI frameworks that do publish the match in the scope.
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
            ctx = _context.RequestContext(
                request_id=uuid.uuid4().hex,
                synthetic=self._is_synthetic(header_map),
            )
            token = _context.push(ctx)
            # Snapshot: the router mutates root_path/path_params in place during the
            # call, and matching must see the request exactly as it arrived.
            scope_snapshot = dict(scope)
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
            body_recorder = _BodyRecorder(receive, self.bench.config.max_body_bytes)
            await body_recorder.prefetch()
            receive_wrapper = body_recorder.receive

        try:
            await self.app(scope, receive_wrapper, send_wrapper)
        finally:
            # Runs after the last response byte has been handed to the server, so the
            # event never sits between the app and the client.
            try:
                self._record(scope_snapshot, scope, headers, header_map, ctx, status["code"], body_recorder)
            except Exception:  # noqa: BLE001
                pass
            _context.pop(token)

    def _is_synthetic(self, header_map: dict[str, str]) -> bool:
        config = self.bench.config
        if config.selftest_header in header_map:
            return True
        seeder = config.seeder_user_agent
        return bool(seeder) and seeder.lower() in header_map.get("user-agent", "").lower()

    def _record(
        self,
        scope_snapshot: dict[str, Any],
        scope: dict[str, Any],
        headers: list[tuple[str, str]],
        header_map: dict[str, str],
        ctx: _context.RequestContext,
        status: int | None,
        body_recorder: "_BodyRecorder | None",
    ) -> None:
        bench = self.bench
        if not bench.config.enabled:
            return
        template, path_params = self._template(scope_snapshot)
        if not path_params:
            # The router filled these in even when our own match came up short
            # (custom Route subclasses, third-party routers).
            path_params = dict(scope.get("path_params") or {})

        collector = bench.new_param_collector()
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

        client = scope_snapshot.get("client") or ()
        method = scope_snapshot.get("method") or ("WEBSOCKET" if scope_snapshot.get("type") == "websocket" else "GET")
        bench.record_request(
            method=method,
            route=template,
            path=scope_snapshot.get("path", ""),
            status=status,
            params=collector.entries,
            auth_subject=auth_subject,
            client_ip=client[0] if client else "",
            user_agent=header_map.get("user-agent", ""),
            request_id=ctx.request_id,
            synthetic=ctx.synthetic,
        )


def _stringify(value: Any) -> str:
    return value if isinstance(value, str) else str(value)


def _declares_body(header_map: dict[str, str]) -> bool:
    """Only touch ``receive`` when the request actually announces a body.

    Reading a body that was never sent would block until the client got bored, which
    is the one way this middleware could add real latency.
    """
    if "chunked" in header_map.get("transfer-encoding", "").lower():
        return True
    try:
        return int(header_map.get("content-length", "0")) > 0
    except ValueError:
        return False


class _BodyRecorder:
    """Buffer the request body for enumeration, then replay it to the application.

    The app must receive its body byte for byte: the buffered messages are replayed in
    order and anything beyond ``max_bytes`` is streamed straight from the original
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
                break  # http.disconnect: replay it, the app must still see it
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
    """Find the object owning the route table, following the ASGI wrapper chain.

    ``app.add_middleware(BenchASGIMiddleware)`` hands us an inner middleware, not the
    application, so the router has to be found by walking ``.app`` / ``.router``.
    """
    for _ in range(max_depth):
        if node is None:
            return None
        routes = getattr(node, "routes", None)
        if isinstance(routes, (list, tuple)):
            return node
        node = getattr(node, "app", None) or getattr(node, "router", None)
    return None
