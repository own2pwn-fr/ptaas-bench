"""WSGI middleware for Flask and bare Werkzeug services.

Records the **rule as registered** (``/api/orders/<int:id>``), never the concrete URL,
for the same reason as the ASGI side: one time series per endpoint, not one per
identifier. The concrete path is kept on the record.

The rule is resolved against Werkzeug's ``url_map`` rather than read from
``flask.request.url_rule``, because Flask pops its request context *before* handing the
response iterable back to a WSGI middleware -- by the time this code runs again, the
request object is gone. Matching the map costs a few microseconds and behaves the same
for a bare Werkzeug application.

Usage::

    app.wsgi_app = TelemetryWSGIMiddleware(app.wsgi_app)   # url_map found via __self__
    app.wsgi_app = TelemetryWSGIMiddleware(app.wsgi_app, framework_app=app)
"""

from __future__ import annotations

import io
import uuid
from typing import Any, Callable, Iterable

from . import _context
from ._client import TelemetryClient, get_telemetry, peer_matches_forwarded_claim
from ._params import collect_body, collect_headers, collect_query, normalise_host

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


class TelemetryWSGIMiddleware:
    """Export exactly one request record per request."""

    def __init__(
        self,
        app: Any,
        *,
        telemetry: TelemetryClient | None = None,
        framework_app: Any = None,
        url_map: Any = None,
        auth_subject: Callable[[dict[str, Any]], str | None] | None = None,
    ) -> None:
        self.app = app
        self._telemetry = telemetry
        self._framework_app = framework_app
        self._url_map = url_map
        self._auth_subject = auth_subject

    @property
    def telemetry(self) -> TelemetryClient:
        return self._telemetry or get_telemetry()

    def _map(self) -> Any:
        if self._url_map is not None:
            return self._url_map
        for candidate in (
            self._framework_app,
            self.app,
            # ``TelemetryWSGIMiddleware(app.wsgi_app)`` is the idiomatic Flask spelling;
            # the bound method still points at the application owning the url_map.
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
            # with one of them recovers the rule, so a 405 lands in its endpoint's time
            # series instead of a nameless bucket.
            for method in getattr(exc, "valid_methods", None) or ():
                try:
                    rule, args = adapter.match(method=method, return_rule=True)
                    return self._prefixed(environ, rule.rule), dict(args)
                except Exception:  # noqa: BLE001
                    continue
            return UNMATCHED, {}

    @staticmethod
    def _prefixed(environ: dict[str, Any], rule: str) -> str:
        # An application mounted under a dispatcher has rules relative to its mount
        # point, while dashboards name the public path, so SCRIPT_NAME goes back in.
        script_name = (environ.get("SCRIPT_NAME") or "").rstrip("/")
        return f"{script_name}{rule}" if script_name else rule

    # -------------------------------------------------------------------- WSGI

    def __call__(self, environ: dict[str, Any], start_response: Callable) -> Iterable[bytes]:
        try:
            headers = environ_headers(environ)
            header_map = dict(headers)
            client_ip = environ.get("REMOTE_ADDR", "")
            peer_ip = self._peer_ip(environ, header_map)
            synthetic = self.telemetry.is_synthetic_peer(peer_ip)
            template, path_params = self._template(environ)
            ctx = _context.RequestContext(
                request_id=uuid.uuid4().hex,
                synthetic=synthetic,
                peer_ip=peer_ip,
                client_ip=client_ip,
                route=template,
            )
            token = _context.push(ctx)
            body = self._capture_body(environ)
        except Exception:  # noqa: BLE001
            return self.app(environ, start_response)

        status: dict[str, int | None] = {"code": None}

        def start_response_wrapper(status_line: str, response_headers, exc_info=None):
            # Response headers are forwarded untouched. An agent that stamps its own
            # header onto every response changes what clients and caches see, and shows
            # up in every capture anyone takes of this service.
            try:
                status["code"] = int(str(status_line).split(" ", 1)[0])
            except (ValueError, IndexError):
                status["code"] = None
            return start_response(status_line, response_headers, exc_info)

        try:
            return self.app(environ, start_response_wrapper)
        finally:
            # The status is known as soon as the application returns; the body bytes
            # are the server's business. Recording here, rather than wrapping the
            # iterable, keeps the response path free of an extra object and cannot lose
            # the record to a server that forgets to call close().
            try:
                self._record(environ, headers, header_map, ctx, template, path_params,
                             client_ip, status["code"], body)
            except Exception:  # noqa: BLE001
                pass
            _context.pop(token)

    def _peer_ip(self, environ: dict[str, Any], header_map: dict[str, str]) -> str:
        """The socket peer, or an empty string when what we have is a caller's claim.

        ProxyFix and friends overwrite REMOTE_ADDR with a header value in place and
        keep the original under ``werkzeug.proxy_fix.orig``; the original is what the
        socket saw, so it is what counts. Neither ``request.remote_addr`` nor any
        forwarded header is consulted, and an address the caller also announced in a
        forwarded header is refused: it classifies nothing here and is not reported as
        a peer to anything downstream either.
        """
        original = environ.get("werkzeug.proxy_fix.orig")
        peer = ""
        if isinstance(original, dict):
            peer = original.get("REMOTE_ADDR") or ""
        peer = peer or environ.get("werkzeug.proxy_fix.orig_remote_addr") or environ.get("REMOTE_ADDR", "")
        if peer_matches_forwarded_claim(peer, header_map):
            return ""
        return peer

    def _capture_body(self, environ: dict[str, Any]) -> bytes:
        """Read the body for attribute extraction and put an equivalent stream back.

        The application must read its body exactly as if nothing had happened, so what
        was consumed is re-injected: a BytesIO when the whole body fits in the buffer,
        otherwise the buffered prefix chained in front of the untouched stream.
        """
        stream = environ.get("wsgi.input")
        if stream is None:
            return b""
        limit = self.telemetry.config.max_body_bytes
        try:
            declared = int(environ.get("CONTENT_LENGTH") or 0)
        except ValueError:
            declared = 0
        if declared <= 0 and not environ.get("wsgi.input_terminated"):
            return b""  # nothing announced: reading would wait on the socket
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
        template: str,
        path_params: dict[str, Any],
        client_ip: str,
        status: int | None,
        body: bytes,
    ) -> None:
        telemetry = self.telemetry
        if not telemetry.config.enabled:
            return
        collector = telemetry.new_param_collector()
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
        telemetry.record_request(
            method=environ.get("REQUEST_METHOD", "GET"),
            route=ctx.route or template,
            host=normalise_host(header_map.get("host")),
            path=f"{script_name}{environ.get('PATH_INFO', '')}",
            status=status,
            params=collector.entries,
            auth_subject=auth_subject,
            client_ip=client_ip,
            user_agent=header_map.get("user-agent", ""),
            request_id=ctx.request_id,
            synthetic=ctx.synthetic,
            peer_ip=ctx.peer_ip,
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
