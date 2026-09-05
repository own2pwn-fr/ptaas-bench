"""HTTP and HTTPS listeners: answer 200 to anything, log everything.

The body is tiny and constant on purpose. A canary that echoed anything back would let
a tool distinguish targets, and a large body would make the canary itself a bandwidth
factor in a timing-sensitive benchmark.

Bytes that are not HTTP at all are still recorded (a payload fired blind at port 80 is
evidence too); the connection then gets a 400 and closes.
"""

from __future__ import annotations

import asyncio
import logging

from ..config import Config
from ..httpwire import HttpParseError, build_response, read_request
from ..recorder import Recorder
from ..tokens import Candidate, first_path_segment, host_label, query_token

log = logging.getLogger("bench_oob.web")

BODY = b"ok\n"


class WebHandler:
    def __init__(self, config: Config, recorder: Recorder, channel: str) -> None:
        self.config = config
        self.recorder = recorder
        self.channel = channel  # "http" or "https"

    async def __call__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername") or ("?", 0)
        peer_ip = peer[0]
        try:
            try:
                request = await read_request(reader)
            except HttpParseError as exc:
                if exc.raw:
                    self.recorder.record(
                        channel=self.channel,
                        source_ip=peer_ip,
                        candidates=[],
                        raw=f"{self.channel} non-http bytes: {exc.raw[:512]!r}",
                        detail={"malformed": True},
                        in_zone=None,
                    )
                    writer.write(build_response(400, b"bad request\n"))
                    await writer.drain()
                return

            host = request.host
            zone = self.config.zone
            hostname = host.split(":", 1)[0].lower() if host else ""
            in_zone = bool(hostname) and (hostname == zone or hostname.endswith("." + zone))

            self.recorder.record(
                channel=self.channel,
                source_ip=peer_ip,
                candidates=[
                    Candidate("host_header", host_label(host, zone) or ""),
                    Candidate("path_segment", first_path_segment(request.path) or ""),
                    Candidate("query_t", query_token(request.query) or ""),
                ],
                raw=request.head_text,
                detail={
                    "method": request.method,
                    "target": request.target,
                    "host": host,
                    "user_agent": request.user_agent,
                    "body_len": len(request.body),
                },
                in_zone=in_zone if hostname else None,
                # Same convention as the target SDKs: traffic the platform generates
                # during seeding or self-test is stored but must never be scored.
                synthetic="x-bench-selftest" in request.headers,
            )
            writer.write(build_response(200, BODY))
            await writer.drain()
        except (ConnectionError, asyncio.CancelledError):
            raise
        except Exception:  # pragma: no cover - never let one client kill the listener
            log.exception("%s handler failed", self.channel)
        finally:
            try:
                writer.close()
            except Exception:  # pragma: no cover
                pass
