"""HTTP and HTTPS listeners: answer like an edge node, record everything.

The response is a plain, constant 200 with an ordinary server banner and cache headers.
Constant on purpose: a body that echoed anything would let a client tell one destination
from another, and a large body would make this node a bandwidth factor in measurements
that are sometimes timing-sensitive.

Bytes that are not HTTP at all are still recorded -- a payload fired blind at port 80 is
worth a line -- and get a 400.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import logging

from ..config import Config
from ..httpwire import HttpParseError, build_response, read_request
from ..recorder import Recorder
from ..tokens import Candidate, first_path_segment, host_label, query_token

log = logging.getLogger("edge_resolver.web")

BODY = b"ok\n"
ETAG = '"%s"' % hashlib.sha1(BODY).hexdigest()[:16]
CACHE_HEADERS = {
    "Cache-Control": "public, max-age=300",
    "Accept-Ranges": "bytes",
    "ETag": ETAG,
}


def _owned(host: str | None, zone: str) -> bool:
    name = (host or "").lower().strip(".")
    return bool(name) and (name == zone or name.endswith("." + zone))


def _is_address(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


class WebHandler:
    def __init__(
        self, config: Config, recorder: Recorder, channel: str, certificates=None
    ) -> None:
        self.config = config
        self.recorder = recorder
        self.channel = channel  # "http" or "https"
        self.certificates = certificates

    async def __call__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername") or ("?", 0)
        peer_ip = peer[0]
        # The name the client asked for over TLS, recovered before anything is read: on
        # the server side of a handshake it is only available through the certificate we
        # chose for it, and it is the one clue a connection that never speaks HTTP still
        # gives us.
        sni = None
        if self.certificates is not None:
            ssl_object = writer.get_extra_info("ssl_object")
            sni = self.certificates.name_for(getattr(ssl_object, "context", None))
        zone = self.config.owned_zone

        try:
            try:
                request = await read_request(reader)
            except HttpParseError as exc:
                if exc.raw or sni:
                    self.recorder.record(
                        channel=self.channel,
                        source_ip=peer_ip,
                        host=sni,
                        candidates=[Candidate("host_header", host_label(sni, zone) or "")],
                        raw=f"{self.channel} non-http bytes: {exc.raw[:512]!r}",
                        detail={"malformed": True, "server_name": sni},
                        owned_zone=_owned(sni, zone) if sni else None,
                    )
                    writer.write(build_response(400, b"", extra_headers={"Connection": "close"}))
                    await writer.drain()
                return

            header_host = request.host
            hostname = header_host.split(":", 1)[0].lower().strip(".") if header_host else ""
            # A Host header holding a bare address says nothing about the destination the
            # payload named; the negotiated server name does. Prefer whichever is a name.
            wanted = hostname if hostname and not _is_address(hostname) else (sni or hostname)

            self.recorder.record(
                channel=self.channel,
                source_ip=peer_ip,
                host=wanted or None,
                candidates=[
                    Candidate("host_header", host_label(wanted, zone) or ""),
                    Candidate("path_segment", first_path_segment(request.path) or ""),
                    Candidate("query_t", query_token(request.query) or ""),
                ],
                raw=request.head_text,
                detail={
                    "method": request.method,
                    "target": request.target,
                    "host": header_host,
                    "server_name": sni,
                    "user_agent": request.user_agent,
                    "body_len": len(request.body),
                },
                owned_zone=_owned(wanted, zone) if wanted else None,
            )
            body = b"" if request.method == "HEAD" else BODY
            writer.write(
                build_response(
                    200,
                    body,
                    extra_headers={**CACHE_HEADERS, "Content-Length": str(len(BODY))},
                )
            )
            await writer.drain()
        except (ConnectionError, asyncio.CancelledError):
            raise
        except Exception:  # pragma: no cover - one client must not kill the listener
            log.exception("%s handling failed", self.channel)
        finally:
            try:
                writer.close()
            except Exception:  # pragma: no cover
                pass
