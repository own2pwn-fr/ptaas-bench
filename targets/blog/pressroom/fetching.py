"""Outbound retrieval: wire pictures and link previews.

Two features need the service to go and get something on a caller's behalf. Both
register the destination with the agent immediately before the request, because the
connection itself only appears in the network's own logs, where nothing says which
request opened it; the pairing is the only thing that lets the two be joined.

``Retrieved.off_list`` says whether the host was one of the partners we label content
from. The counters that use it are raised by the callers, after the bytes are actually
in a response, because "we fetched it" and "the caller read it" are different facts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from .observability import telemetry
from .settings import settings

MAX_BODY = 262_144


class RetrievalError(ValueError):
    """The address could not be used."""


@dataclass
class Retrieved:
    url: str
    host: str
    status: int
    content_type: str
    body: bytes
    off_list: bool

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", "replace")


def retrieve(url: str, *, providers: tuple[str, ...], signal: str, param: str,
             accept: str = "*/*") -> Retrieved:
    parts = urlsplit((url or "").strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise RetrievalError("Give a http:// or https:// address.")
    host = parts.hostname
    off_list = host not in providers

    # Registered before the request goes out, not after: the name lookup follows
    # within microseconds and anything recorded afterwards arrives behind the effect
    # it explains.
    telemetry.outbound(url, signal=signal, param=param)

    cfg = settings()
    try:
        with httpx.Client(timeout=cfg.outbound_timeout_s, follow_redirects=True,
                          max_redirects=3) as client:
            response = client.get(url, headers={
                "accept": accept,
                "user-agent": f"{cfg.site_name} retrieval/1.4 (+https://{cfg.site_domain}/about)",
            })
            body = response.content[:MAX_BODY]
    except httpx.HTTPError as error:
        raise RetrievalError(f"Could not reach {host}.") from error

    return Retrieved(
        url=url, host=host, status=response.status_code,
        content_type=response.headers.get("content-type", ""),
        body=body, off_list=off_list,
    )


def account_reflected(result: Retrieved, signal: str, *, served_bytes: int) -> None:
    """Raise the count when a caller was actually handed what we retrieved.

    Both halves matter. A fetch to a partner is the feature. A fetch that returned
    nothing to the caller is a fetch, not a reading primitive.
    """
    if not result.off_list or served_bytes <= 0:
        return
    telemetry.signal(signal, {
        "payload": result.url[:200],
        "detail": (f"retrieved {served_bytes} bytes from {result.host} "
                   f"(status {result.status}, {result.content_type or 'no type'}) "
                   "and placed them in the response"),
    })


def describe(result: Retrieved) -> dict[str, Any]:
    """The metadata a link preview shows, pulled out of what came back."""
    text = result.text
    title = _between(text, "<title>", "</title>") or result.host
    description = _meta(text, "description") or ""
    return {
        "url": result.url,
        "host": result.host,
        "status": result.status,
        "content_type": result.content_type,
        "title": title.strip()[:200],
        "description": description.strip()[:400],
        # Kept short on purpose, but it is the document: previews of feeds and JSON
        # documents have no title to show and the desk still needs to see something.
        "excerpt": text[:600],
    }


def _between(text: str, start: str, end: str) -> str:
    lowered = text.lower()
    left = lowered.find(start)
    if left < 0:
        return ""
    right = lowered.find(end, left + len(start))
    if right < 0:
        return ""
    return text[left + len(start):right]


def _meta(text: str, name: str) -> str:
    import re

    match = re.search(
        r"""<meta[^>]+name=["']?""" + name + r"""["']?[^>]+content=["']([^"']*)""",
        text, re.IGNORECASE)
    return match.group(1) if match else ""
