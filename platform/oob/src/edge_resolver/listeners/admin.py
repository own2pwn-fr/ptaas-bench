"""Admin API on :8901 -- internal network only.

It exists for the platform's self-tests: assert that a seeded request really produced an
observation, without going through the reporting database. It lists what has been
observed and how it was attributed, so a client that could read it would be reading part
of the answer.

Two locks, not one. It binds to the internal interface (see Config.admin_host), and it
answers only addresses on the allowlist -- because the applications sit on that internal
network too, and an application is exactly what a client takes control of first. A
refused caller gets the same 404 as an unknown path, so there is nothing to notice.

    GET  /observations?since=<seq>[&limit=][&wait=<seconds>]   (alias: /callbacks)
    POST /hints        register a correlation hint locally
    POST /reset
    GET  /stats
    GET  /healthz
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
from urllib.parse import parse_qs

from ..config import Config
from ..correlation import CorrelationIndex
from ..httpwire import HttpParseError, build_response, read_request
from ..store import ObservationStore

log = logging.getLogger("edge_resolver.admin")


class AdminHandler:
    def __init__(
        self,
        config: Config,
        store: ObservationStore,
        telemetry,
        index: CorrelationIndex,
        poller=None,
        worker=None,
        networks=(),
    ) -> None:
        self.config = config
        self.store = store
        self.telemetry = telemetry
        self.index = index
        self.poller = poller
        self.worker = worker
        self.networks = tuple(networks)

    def allows(self, address: str) -> bool:
        if not self.networks:
            return True
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError:
            return False
        return any(parsed in network for network in self.networks)

    async def __call__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername") or ("?", 0)
        try:
            if not self.allows(peer[0]):
                log.warning("refused admin request from %s", peer[0])
                writer.write(
                    build_response(
                        404, b'{"error":"not found"}\n', content_type="application/json"
                    )
                )
                await writer.drain()
                return
            try:
                request = await read_request(reader)
            except HttpParseError:
                writer.write(
                    build_response(400, b'{"error":"bad request"}\n', content_type="application/json")
                )
                await writer.drain()
                return
            status, payload = await self._route(request)
            body = (json.dumps(payload, default=str) + "\n").encode()
            writer.write(build_response(status, body, content_type="application/json"))
            await writer.drain()
        except (ConnectionError, asyncio.CancelledError):
            raise
        except Exception:  # pragma: no cover
            log.exception("admin request failed")
        finally:
            try:
                writer.close()
            except Exception:  # pragma: no cover
                pass

    async def _route(self, request) -> tuple[int, dict]:
        path = request.path.rstrip("/") or "/"
        query = parse_qs(request.query)

        # /callbacks is kept as an alias so self-tests written against the first
        # iteration of this API keep working.
        if path in ("/observations", "/callbacks") and request.method == "GET":
            since = _int(query.get("since"), 0)
            limit = min(_int(query.get("limit"), 1000), 5000)
            wait = min(_float(query.get("wait"), 0.0), 30.0)
            if wait > 0:
                # Long-poll variant, so a self-test can await an observation without
                # busy-looping over the API.
                await asyncio.get_running_loop().run_in_executor(
                    None, self.store.wait_for, 1, wait, since
                )
            items = self.store.since(since, limit=limit)
            payload = [item.as_json() for item in items]
            return 200, {
                "since": since,
                "next_seq": items[-1].seq if items else since,
                "last_seq": self.store.last_seq(),
                "count": len(items),
                "observations": payload,
                "callbacks": payload,  # alias, same objects
            }

        if path == "/hints" and request.method == "POST":
            try:
                body = json.loads(request.body or b"{}")
            except ValueError:
                return 400, {"error": "invalid json"}
            payloads = body.get("correlations") or ([body] if body else [])
            added = self.index.add_payloads(payloads)
            return 200, {"added": added}

        if path == "/reset" and request.method == "POST":
            # The sequence restarts too, so a self-test can always page from since=0.
            self.store.reset()
            return 200, {"ok": True}

        if path == "/stats" and request.method == "GET":
            return 200, {
                "zone": self.config.zone,
                "stored": len(self.store),
                "last_seq": self.store.last_seq(),
                "telemetry": self.telemetry.stats.as_json(),
                "telemetry_url": self.config.telemetry_url or None,
                "correlation": self.index.stats(),
                "poller": self.poller.stats() if self.poller is not None else None,
                "attribution": self.worker.stats() if self.worker is not None else None,
            }

        if path == "/healthz":
            return 200, {"ok": True}

        return 404, {"error": "not found", "path": path}


def _int(values, default: int) -> int:
    try:
        return int(values[0]) if values else default
    except (TypeError, ValueError):
        return default


def _float(values, default: float) -> float:
    try:
        return float(values[0]) if values else default
    except (TypeError, ValueError):
        return default
