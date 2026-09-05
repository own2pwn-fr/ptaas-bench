"""WSGI middleware for Flask / Werkzeug targets.

Reports the **rule as registered** (``/api/orders/<int:id>``), never the concrete URL,
for the same reason as the ASGI side: the catalog's entrypoints are templates.

The rule is reported exactly as Flask registered it, converters included; normalising
the three spellings the platform sees (``<int:id>`` here, ``{id}`` in Starlette, ``:id``
in the catalog) is the scorer's job, since it is the only component that reads the
catalog and can therefore be the only place that mapping is defined.

The rule is resolved with Werkzeug's own ``url_map`` rather than by reading
``flask.request.url_rule``, because Flask pops its request context *before* returning
the response iterable to us -- by the time a WSGI middleware regains control the
request object is already gone. Matching the map ourselves is a few microseconds and
works identically for a bare Werkzeug app.

Usage::

    app.wsgi_app = BenchWSGIMiddleware(app.wsgi_app)      # url_map found via __self__
    app.wsgi_app = BenchWSGIMiddleware(app.wsgi_app, framework_app=app)
"""

from __future__ import annotations

import io
import uuid
from typing import Any, Callable, Iterable

from . import _context
from ._client import BenchClient, get_bench
from ._params import collect_body, collect_headers, collect_query

UNMATCHED = "<unmatched>"

# CGI-style keys that carry a header without the HTTP_ prefix.
_UNPREFIXED = {"CONTENT_TYPE": "content-type", "CONTENT_LENGTH": "content-length"}


def environ_headers(environ: dict[str, Any]) -> list[tuple[str, str]]:
    headers: list[tuple[str, str]] = []
    for key, value in environ.items():
        if not isinstance(value, str):
            continue
        if key.startswith("HTTP_"):
            headers.append((key[5:].replace("_", "-").lower(), value))
        elif key in _UNPREFIXED:
            headers.append((_UNPREFIXED[key], value))
    return headers


class BenchWSGIMiddleware:
    """Emit exactly one ``http_request`` event per request."""

    def __init__(
        self,
        app: Any,
        *,
        bench: BenchClient | None = None,
        framework_app: Any = None,
        url_map: Any = None,
        auth_subject: Callable[[dict[str, Any]], str | None] | None = None,
    ) -> None:
        self.app = app
        self._bench = bench
        self._framework_app = framework_app
        self._url_map = url_map
        self._auth_subject = auth_subject

    @property
    def bench(self) -> BenchClient:
        return self._bench or get_bench()

    def _map(self) -> Any:
        if self._url_map is not None:
            return self._url_map
        for candidate in (
            self._framework_app,
            self.app,
            # ``BenchWSGIMiddleware(app.wsgi_app)`` is the idiomatic Flask spelling; the
            # bound method still points at the Flask app that owns the url_map.
            getattr(self.app, "__self__", None),
        ):
            url_map = getattr(candidate, "url_map", None)
            if url_map is not None:
                self._url_map = url_map
                return url_map
        return None

    def _template(self, environ: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        url_map = self._map()
        if url_map is None:
            return UNMATCHED, {}
        try:
            adapter = url_map.bind_to_environ(environ)
        except Exception:  # noqa: BLE001
            return UNMATCHED, {}
        try:
            rule, args = adapter.match(return_rule=True)
            return self._prefixed(environ, rule.rule), dict(args)
        except Exception as exc:  # noqa: BLE001
            # MethodNotAllowed carries the methods the path *does* accept; re-matching
            # with one of them recovers the template. A 405 still proves the tool
            # reached the endpoint, so it must not be reported as <unmatched>.
            for method in getattr(exc, "valid_methods", None) or ():
                try:
                    rule, args = adapter.match(method=method, return_rule=True)
                    return self._prefixed(environ, rule.rule), dict(args)
                except Exception:  # noqa: BLE001
                    continue
            return UNMATCHED, {}

    @staticmethod
    def _prefixed(environ: dict[str, Any], rule: str) -> str:
        # A Flask app mounted under DispatcherMiddleware has rules relative to its
        # mount point; the catalog names the public path, so SCRIPT_NAME goes back in.
        script_name = (environ.get("SCRIPT_NAME") or "").rstrip("/")
        return f"{script_name}{rule}" if script_name else rule

    # -------------------------------------------------------------------- WSGI

    def __call__(self, environ: dict[str, Any], start_response: Callable) -> Iterable[bytes]:
        try:
            headers = environ_headers(environ)
            header_map = dict(headers)
            ctx = _context.RequestContext(
                request_id=uuid.uuid4().hex,
                synthetic=self._is_synthetic(header_map),
            )
            token = _context.push(ctx)
            body = self._capture_body(environ)
        except Exception:  # noqa: BLE001
            return self.app(environ, start_response)

        status: dict[str, int | None] = {"code": None}

        def start_response_wrapper(status_line: str, response_headers, exc_info=None):
            # Response headers are forwarded untouched: adding anything here would let
            # the tool under test fingerprint the instrumentation.
            try:
                status["code"] = int(str(status_line).split(" ", 1)[0])
            except (ValueError, IndexError):
                status["code"] = None
            return start_response(status_line, response_headers, exc_info)

        try:
            return self.app(environ, start_response_wrapper)
        finally:
            # The status is known as soon as the app returns; the body bytes are the
            # server's business. Emitting here (rather than wrapping the iterable)
            # keeps the response path free of an extra object and cannot lose the
            # event to a server that forgets to call close().
            try:
                self._record(environ, headers, header_map, ctx, status["code"], body)
            except Exception:  # noqa: BLE001
                pass
            _context.pop(token)

    def _is_synthetic(self, header_map: dict[str, str]) -> bool:
        config = self.bench.config
        if config.selftest_header in header_map:
            return True
        seeder = config.seeder_user_agent
        return bool(seeder) and seeder.lower() in header_map.get("user-agent", "").lower()

    def _capture_body(self, environ: dict[str, Any]) -> bytes:
        """Read the body for enumeration and put an equivalent stream back.

        The application must be able to read its body exactly as if nothing happened,
        so what was consumed is re-injected: a BytesIO when the whole body fits in the
        buffer, otherwise the buffered prefix chained in front of the untouched stream.
        """
        stream = environ.get("wsgi.input")
        if stream is None:
            return b""
        limit = self.bench.config.max_body_bytes
        try:
            declared = int(environ.get("CONTENT_LENGTH") or 0)
        except ValueError:
            declared = 0
        if declared <= 0 and not environ.get("wsgi.input_terminated"):
            return b""  # no body announced: never read, or we would block on the socket
        to_read = min(declared, limit) if declared > 0 else limit
        body = stream.read(to_read) or b""
        if declared > 0 and len(body) >= declared:
            environ["wsgi.input"] = io.BytesIO(body)
        else:
            environ["wsgi.input"] = _ChainedInput(body, stream)
        return body

    def _record(
        self,
        environ: dict[str, Any],
        headers: list[tuple[str, str]],
        header_map: dict[str, str],
        ctx: _context.RequestContext,
        status: int | None,
        body: bytes,
    ) -> None:
        bench = self.bench
        if not bench.config.enabled:
            return
        template, path_params = self._template(environ)
        collector = bench.new_param_collector()
        collect_query(collector, environ.get("QUERY_STRING", ""))
        collector.add_many(((str(k), v if isinstance(v, str) else str(v)) for k, v in path_params.items()), "path")
        collect_headers(collector, headers)
        collect_body(collector, body, header_map.get("content-type", ""))
        collector.extend_entries(ctx.extra_params)

        auth_subject = ctx.auth_subject
        if auth_subject is None and self._auth_subject is not None:
            try:
                auth_subject = self._auth_subject(environ)
            except Exception:  # noqa: BLE001
                auth_subject = None

        script_name = (environ.get("SCRIPT_NAME") or "").rstrip("/")
        bench.record_request(
            method=environ.get("REQUEST_METHOD", "GET"),
            route=template,
            path=f"{script_name}{environ.get('PATH_INFO', '')}",
            status=status,
            params=collector.entries,
            auth_subject=auth_subject,
            client_ip=environ.get("REMOTE_ADDR", ""),
            user_agent=header_map.get("user-agent", ""),
            request_id=ctx.request_id,
            synthetic=ctx.synthetic,
        )


class _ChainedInput:
    """A prefix already read, followed by the rest of the original stream."""

    def __init__(self, head: bytes, rest: Any) -> None:
        self._head = io.BytesIO(head)
        self._rest = rest

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            return self._head.read() + self._rest.read()
        data = self._head.read(size)
        if len(data) < size:
            data += self._rest.read(size - len(data))
        return data

    def readline(self, size: int = -1) -> bytes:
        line = self._head.readline(size)
        if line.endswith(b"\n") or (size is not None and size >= 0 and len(line) >= size):
            return line
        remaining = -1 if (size is None or size < 0) else size - len(line)
        return line + self._rest.readline(remaining)

    def readlines(self, hint: int = -1) -> list[bytes]:
        return list(iter(self.readline, b""))

    def __iter__(self):
        return iter(self.readline, b"")

    def close(self) -> None:
        try:
            self._rest.close()
        except Exception:  # noqa: BLE001
            pass
