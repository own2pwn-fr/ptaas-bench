"""HTTP from inside the platform, because there is no way in from outside.

Two separate problems this solves, and they have different answers.

**Reaching the collector.** It publishes no port, and its run management, event
export, stats and correlation endpoints answer 404 -- not 403, because a refusal
confirms there is something to refuse -- to every source except its own loopback and
the sinkhole. That is deliberate and it is not merely network separation: targets are
dual-homed, and a target is exactly what a tool takes RCE on, so being "on the
internal network" was never protection for the answer key. The orchestrator therefore
executes inside the collector's own container and talks to 127.0.0.1.

**Reaching a target, over the right interface.** The platform's own traffic (a login the harness performs on
behalf of a scanner that cannot log itself in) must not be scored as the tool's. The
collector and the target SDKs classify synthetic traffic *by source address*, never
by a header -- a header would be visible to a tool through any reflection or verbose
error and would hand it the shape of the grader. So harness traffic aimed at a target
goes through the dual-homed sinkhole, whose address sits in the range both sides
treat as the platform's own. That is necessary but not sufficient: a target is
dual-homed too, and usually answers to the same name on both networks, so resolving
that name picks an interface at random -- and the interface decides the source
address, which decides the classification. ``connect_to`` pins the connection to the
target's internal address while keeping the Host header, which makes the routing a
decision rather than a coin toss.

Two transports, same interface:

* ``ExecHttp``   -- `docker compose exec -T <service> python -c ...`. The default,
  instantiated once per role (collector, platform client).
* ``DirectHttp`` -- plain urllib, for running the orchestrator from inside the
  networks. Never the default, because the default must be the one that cannot
  accidentally attribute our traffic to the tool.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Any, Protocol
from urllib.parse import urlsplit

log = logging.getLogger("bench.runners.http")

# Runs inside the collector container. Kept deliberately tiny and dependency-free:
# it must work in any image that has a Python interpreter, and it must be readable
# by a reviewer checking that the harness cannot be used as a proxy into the
# internal network by anything other than the operator.
_CLIENT_SRC = r"""
import json, sys, urllib.request, urllib.error
from urllib.parse import urlsplit
spec = json.loads(sys.argv[1])
data = spec.get("body")
if data is not None:
    data = data.encode()
url = spec["url"]
headers = dict(spec.get("headers") or {})
connect_to = spec.get("connect_to")
if connect_to:
    # Connect to a specific address while keeping the Host header. A target is
    # dual-homed under the same name, so resolving it would pick either interface at
    # random -- and which one decides whether this request counts as the platform's
    # own traffic or as the tool's.
    parts = urlsplit(url)
    headers.setdefault("Host", parts.netloc)
    port = f":{parts.port}" if parts.port else ""
    url = parts._replace(netloc=f"{connect_to}{port}").geturl()
req = urllib.request.Request(url, data=data, headers=headers, method=spec["method"])
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
        connect_to: str | None = None,
    ) -> Response: ...


class ExecHttp:
    """Perform the request from inside a container that is already where it needs to be.

    ``service`` decides whose address the request appears to come from, which is the
    whole point: the collector for control-plane calls, the sinkhole for anything
    aimed at a target.
    """

    def __init__(self, docker: Any, service: str = "otel-collector"):
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
        connect_to: str | None = None,
    ) -> Response:
        spec: dict[str, Any] = {"method": method.upper(), "url": url, "timeout": timeout}
        if connect_to:
            spec["connect_to"] = connect_to
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
        connect_to: str | None = None,
    ) -> Response:
        payload: bytes | None = None
        head = dict(headers or {})
        if json_body is not None:
            payload = json.dumps(json_body).encode()
            head.setdefault("Content-Type", "application/json")
        elif data is not None:
            payload = data.encode()
            head.setdefault("Content-Type", "application/x-www-form-urlencoded")
        if connect_to:
            parts = urlsplit(url)
            head.setdefault("Host", parts.netloc)
            port = f":{parts.port}" if parts.port else ""
            url = parts._replace(netloc=f"{connect_to}{port}").geturl()
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
