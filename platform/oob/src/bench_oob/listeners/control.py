"""Control API on :8901 -- bench-internal only.

Exists for the platform's self-tests: assert that a seeded payload really produced a
callback, without going through the collector's database. It is bound to the internal
interface (see Config.control_host) because it lists which tokens have fired, and a
tool that could read it would be reading part of the answer key.

    GET  /callbacks?since=<seq>[&limit=][&wait=<seconds>]
    POST /reset
    GET  /stats
    GET  /healthz
"""

from __future__ import annotations

import asyncio
import json
import logging
from urllib.parse import parse_qs

from ..config import Config
from ..httpwire import HttpParseError, build_response, read_request
from ..store import CallbackStore

log = logging.getLogger("bench_oob.control")


class ControlHandler:
    def __init__(self, config: Config, store: CallbackStore, collector) -> None:
        self.config = config
        self.store = store
        self.collector = collector

    async def __call__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            try:
                request = await read_request(reader)
            except HttpParseError:
                writer.write(build_response(400, b'{"error":"bad request"}\n', content_type="application/json"))
                await writer.drain()
                return
            status, payload = await self._route(request)
            body = (json.dumps(payload, default=str) + "\n").encode()
            writer.write(build_response(status, body, content_type="application/json"))
            await writer.drain()
        except (ConnectionError, asyncio.CancelledError):
            raise
        except Exception:  # pragma: no cover
            log.exception("control handler failed")
        finally:
            try:
                writer.close()
            except Exception:  # pragma: no cover
                pass

    async def _route(self, request) -> tuple[int, dict]:
        path = request.path.rstrip("/") or "/"
        query = parse_qs(request.query)

        if path == "/callbacks" and request.method == "GET":
            since = _int(query.get("since"), 0)
            limit = min(_int(query.get("limit"), 1000), 5000)
            wait = min(_float(query.get("wait"), 0.0), 30.0)
            if wait > 0:
                # Long-poll variant so a self-test can await a callback without
                # busy-looping over the API.
                await asyncio.get_running_loop().run_in_executor(
                    None, self.store.wait_for, 1, wait, since
                )
            items = self.store.since(since, limit=limit)
            return 200, {
                "since": since,
                "next_seq": items[-1].seq if items else since,
                "last_seq": self.store.last_seq(),
                "count": len(items),
                "callbacks": [c.as_json() for c in items],
            }

        if path == "/reset" and request.method == "POST":
            # Sequence restarts at 0 too, so a self-test can always page from since=0.
            self.store.reset()
            return 200, {"ok": True}

        if path == "/stats" and request.method == "GET":
            return 200, {
                "domain": self.config.domain,
                "stored": len(self.store),
                "last_seq": self.store.last_seq(),
                "collector": self.collector.stats.as_json(),
                "collector_url": self.config.collector_url or None,
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
