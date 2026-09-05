"""A very small HTTP/1.1 server-side codec over asyncio streams.

Why not http.server: these listeners answer 200 to *anything*, including bytes that are
not HTTP at all (a client blasting a payload at port 80 still deserves to be logged),
and they share one event loop with four other protocols. Parsing the request line and
headers by hand is a page of code and keeps that behaviour explicit.

Limits: no keep-alive (every response closes the connection, and says so), no chunked
request decoding (a chunked body is read as raw bytes, not reassembled), no HTTP/2.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from email.utils import formatdate
from urllib.parse import unquote, urlsplit

HEAD_LIMIT = 16384
BODY_LIMIT = 65536


class HttpParseError(ValueError):
    def __init__(self, raw: bytes) -> None:
        super().__init__("not an HTTP request")
        self.raw = raw


@dataclass
class HttpRequest:
    method: str
    target: str
    version: str
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""
    head_text: str = ""

    @property
    def path(self) -> str:
        # An absolute-form target (proxy style, "GET http://host/p") is common from
        # server-side request payloads, so split it properly instead of assuming
        # origin-form.
        split = urlsplit(self.target)
        return unquote(split.path or "/")

    @property
    def query(self) -> str:
        return urlsplit(self.target).query

    @property
    def host(self) -> str:
        # Absolute-form target wins over the Host header when both are present, because
        # that is what the client actually asked for.
        authority = urlsplit(self.target).hostname
        return authority or self.headers.get("host", "")

    @property
    def user_agent(self) -> str:
        return self.headers.get("user-agent", "")


async def read_request(
    reader: asyncio.StreamReader,
    *,
    timeout: float = 10.0,
    head_limit: int = HEAD_LIMIT,
    body_limit: int = BODY_LIMIT,
) -> HttpRequest:
    try:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout)
    except asyncio.IncompleteReadError as exc:
        head = exc.partial
    except (asyncio.LimitOverrunError, ValueError):
        head = await reader.read(head_limit)
    except (asyncio.TimeoutError, TimeoutError):
        head = b""
    if not head:
        raise HttpParseError(b"")

    text = head[:head_limit].decode("latin-1")
    lines = text.replace("\r\n", "\n").split("\n")
    parts = lines[0].split(" ")
    if len(parts) < 2 or not parts[0].isalpha():
        raise HttpParseError(head[:head_limit])

    headers: dict[str, str] = {}
    for line in lines[1:]:
        if not line.strip():
            break
        name, sep, value = line.partition(":")
        if not sep:
            continue
        key = name.strip().lower()
        value = value.strip()
        headers[key] = f"{headers[key]}, {value}" if key in headers else value

    request = HttpRequest(
        method=parts[0].upper(),
        target=parts[1],
        version=parts[2] if len(parts) > 2 else "HTTP/1.0",
        headers=headers,
        head_text=text.strip(),
    )

    length = headers.get("content-length")
    if length and length.isdigit():
        want = min(int(length), body_limit)
        if want:
            try:
                request.body = await asyncio.wait_for(reader.readexactly(want), timeout)
            except (asyncio.IncompleteReadError, asyncio.TimeoutError, TimeoutError):
                request.body = b""
    elif "chunked" in headers.get("transfer-encoding", "").lower():
        try:
            request.body = await asyncio.wait_for(reader.read(body_limit), timeout)
        except (asyncio.TimeoutError, TimeoutError):
            request.body = b""
    return request


REASONS = {200: "OK", 400: "Bad Request", 404: "Not Found", 405: "Method Not Allowed"}


def http_date() -> str:
    return formatdate(usegmt=True)


def build_response(
    status: int = 200,
    body: bytes = b"",
    *,
    content_type: str = "text/plain; charset=utf-8",
    extra_headers: dict[str, str] | None = None,
    server: str = "nginx",
) -> bytes:
    reason = REASONS.get(status, "OK")
    headers = {
        "Server": server,
        "Date": http_date(),
        "Content-Type": content_type,
        "Content-Length": str(len(body)),
        "Connection": "close",
        **(extra_headers or {}),
    }
    head = f"HTTP/1.1 {status} {reason}\r\n" + "".join(f"{k}: {v}\r\n" for k, v in headers.items())
    return head.encode("latin-1") + b"\r\n" + body
