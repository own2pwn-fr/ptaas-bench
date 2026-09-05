"""HTTP access to services that live on the `internal: true` bench-internal network.

The collector and the targets' control endpoints are deliberately unreachable from
the host: `bench-internal` is declared `internal: true` and nothing publishes a port.
That is not an accident to work around, it is the property that makes the scores
meaningful -- a scanner on bench-public cannot read the answer key or forge trigger
events, and a harness that punched a hole in that isolation for its own convenience
would quietly invalidate every published number.

So the orchestrator does not open a hole. It borrows the network namespace of a
container that is already legitimately on the network (the collector) and performs
the request from there, using nothing but the Python standard library that container
already ships. No extra image, no published port, no new attack path.

Two transports, same interface:

* ``ExecHttp``   -- `docker compose exec -T collector python -c ...`. The default.
* ``DirectHttp`` -- plain urllib. For running the orchestrator from inside the
  network (e.g. as a container itself) or against a dev stack that does publish a
  port. Never the default, because the default must be the safe one.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Any, Protocol

log = logging.getLogger("bench.runners.http")

# Runs inside the collector container. Kept deliberately tiny and dependency-free:
# it must work in any image that has a Python interpreter, and it must be readable
# by a reviewer checking that the harness cannot be used as a proxy into the
# internal network by anything other than the operator.
_CLIENT_SRC = r"""
import json, sys, urllib.request, urllib.error
spec = json.loads(sys.argv[1])
data = spec.get("body")
if data is not None:
    data = data.encode()
req = urllib.request.Request(
    spec["url"], data=data, headers=spec.get("headers") or {}, method=spec["method"]
)
def collect(resp):
    # Set-Cookie must be read as a list: a login that sets a session cookie and a
    # CSRF cookie collapses to one header if you use a plain dict.
    return {
        "headers": {k.lower(): v for k, v in resp.headers.items()},
        "cookies": resp.headers.get_all("Set-Cookie") or [],
    }
try:
    with urllib.request.urlopen(req, timeout=spec.get("timeout", 30)) as resp:
        out = {"status": resp.status, "body": resp.read().decode("utf-8", "replace")}
        out.update(collect(resp))
except urllib.error.HTTPError as exc:
    out = {"status": exc.code, "body": exc.read().decode("utf-8", "replace")}
    out.update(collect(exc))
except Exception as exc:  # noqa: BLE001 - the caller needs the text, not the type
    out = {"status": 0, "body": "", "error": "%s: %s" % (type(exc).__name__, exc)}
sys.stdout.write(json.dumps(out))
"""


class HttpError(RuntimeError):
    pass


@dataclass
class Response:
    status: int
    body: str
    error: str | None = None
    headers: dict[str, str] = dc_field(default_factory=dict)
    # Raw Set-Cookie lines, in order. The harness logs tools in for scanners that
    # cannot log themselves in, so it has to keep every cookie the app sets.
    cookies: list[str] = dc_field(default_factory=list)

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def json(self) -> Any:
        try:
            return json.loads(self.body)
        except json.JSONDecodeError as exc:
            raise HttpError(f"expected JSON, got {self.body[:200]!r}") from exc

    def check(self, what: str) -> Response:
        if not self.ok:
            raise HttpError(f"{what}: HTTP {self.status} {self.error or self.body[:300]}")
        return self


class Http(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        json_body: Any = None,
        data: str | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 30,
    ) -> Response: ...


class ExecHttp:
    """Perform the request from inside a container already on bench-internal."""

    def __init__(self, docker: Any, service: str = "collector"):
        self.docker = docker
        self.service = service

    def request(
        self,
        method: str,
        url: str,
        *,
        json_body: Any = None,
        data: str | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 30,
    ) -> Response:
        spec: dict[str, Any] = {"method": method.upper(), "url": url, "timeout": timeout}
        head = dict(headers or {})
        if json_body is not None:
            spec["body"] = json.dumps(json_body)
            head.setdefault("Content-Type", "application/json")
        elif data is not None:
            # A form login is url-encoded, not JSON; the caller sets the type.
            spec["body"] = data
            head.setdefault("Content-Type", "application/x-www-form-urlencoded")
        spec["headers"] = head
        res = self.docker.compose_exec(
            self.service,
            ["python", "-c", _CLIENT_SRC, json.dumps(spec)],
            timeout=timeout + 30,
        )
        if res.returncode != 0:
            return Response(0, "", f"docker exec failed: {res.stderr.strip()[:300]}")
        try:
            payload = json.loads(res.stdout.strip() or "{}")
        except json.JSONDecodeError:
            return Response(0, "", f"unparsable client output: {res.stdout[:300]!r}")
        return Response(
            status=int(payload.get("status", 0)),
            body=str(payload.get("body", "")),
            error=payload.get("error"),
            headers=dict(payload.get("headers") or {}),
            cookies=list(payload.get("cookies") or []),
        )


class DirectHttp:
    """Plain urllib. Only usable when the caller can already route to the service."""

    def request(
        self,
        method: str,
        url: str,
        *,
        json_body: Any = None,
        data: str | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 30,
    ) -> Response:
        payload: bytes | None = None
        head = dict(headers or {})
        if json_body is not None:
            payload = json.dumps(json_body).encode()
            head.setdefault("Content-Type", "application/json")
        elif data is not None:
            payload = data.encode()
            head.setdefault("Content-Type", "application/x-www-form-urlencoded")
        req = urllib.request.Request(url, data=payload, headers=head, method=method.upper())
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return Response(
                    resp.status,
                    resp.read().decode("utf-8", "replace"),
                    headers={k.lower(): v for k, v in resp.headers.items()},
                    cookies=resp.headers.get_all("Set-Cookie") or [],
                )
        except urllib.error.HTTPError as exc:
            return Response(
                exc.code,
                exc.read().decode("utf-8", "replace"),
                headers={k.lower(): v for k, v in exc.headers.items()},
                cookies=exc.headers.get_all("Set-Cookie") or [],
            )
        except Exception as exc:  # noqa: BLE001
            return Response(0, "", f"{type(exc).__name__}: {exc}")
