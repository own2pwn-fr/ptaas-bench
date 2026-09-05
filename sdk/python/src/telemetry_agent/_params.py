"""Request attribute extraction: turn a raw request into the ``params`` array of a record.

An error or latency record is only actionable if it says which inputs the handler saw,
so every location is described: query string, body (JSON, form, multipart), path
variables, cookies and the headers a handler may key behaviour off. Everything is
parsed from the raw bytes rather than from the framework's parsed view, because the
framework only exposes what the handler asked for, and the interesting requests are
usually the ones where the handler asked for the wrong thing.

Values are described as ``(sha256, length, 256-character sample)`` rather than stored
verbatim: the hash makes it possible to group requests that carried the same value, and
to tell a default value from an unusual one, without keeping user data in the pipeline.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Iterator
from urllib.parse import parse_qsl

SAMPLE_MAX = 256

# Nesting and volume guards. Documents of a few megabytes with deep nesting do arrive;
# extraction runs on the request path, so its cost is bounded by construction.
JSON_DEPTH_MAX = 16
DEFAULT_MAX_PARAMS = 1024

# Headers worth describing: the ones a handler, a proxy or a cache may key behaviour
# off. Everything ``x-*`` is included because custom headers are where per-tenant and
# feature-toggle routing lives.
DESCRIBED_HEADERS = frozenset(
    {
        "host",
        "referer",
        "user-agent",
        "origin",
        "content-type",
        "accept-language",
        "authorization",
        "forwarded",
        "true-client-ip",
    }
)


def is_described_header(name: str) -> bool:
    lowered = name.lower()
    return lowered in DESCRIBED_HEADERS or lowered.startswith("x-")


def sha256_of(value: str | bytes) -> str:
    if isinstance(value, str):
        # surrogatepass keeps lone surrogates (which percent-decoding can produce)
        # hashable instead of raising on the request path.
        value = value.encode("utf-8", "surrogatepass")
    return hashlib.sha256(value).hexdigest()


def _to_text(value: str | bytes) -> str:
    if isinstance(value, str):
        return value
    return value.decode("utf-8", "replace")


def describe_param(name: str, location: str, value: str | bytes) -> dict[str, Any]:
    """Describe one input. ``value_len`` counts bytes for binary values."""
    text = _to_text(value)
    return {
        "name": name,
        "in": location,
        "value_sha256": sha256_of(value),
        "value_len": len(value) if isinstance(value, bytes) else len(text),
        "sample": text[:SAMPLE_MAX],
    }


def json_scalar_to_text(value: Any) -> str:
    """Render a JSON leaf the way it looked on the wire.

    ``"laptop"`` must hash to sha256(b"laptop"), and a number must hash like its
    textual form, so that the same value carried as JSON, as a form field or as a query
    parameter groups together. Hence: strings raw, everything else via json.dumps
    (``true`` / ``null`` / ``1001``).
    """
    if isinstance(value, str):
        return value
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    try:
        return json.dumps(value)
    except (TypeError, ValueError):
        return str(value)


def flatten_json(value: Any, prefix: str = "", depth: int = 0) -> Iterator[tuple[str, str]]:
    """Yield ``(dotted_path, text_value)`` for every leaf of a decoded JSON document.

    ``{"filter": {"tags": ["a"]}}`` yields ``filter.tags.0``. Empty containers are
    yielded as leaves so their *name* still appears as an observed input.
    """
    if depth > JSON_DEPTH_MAX:
        return
    if isinstance(value, dict):
        if not value:
            yield (prefix or "body", "{}")
            return
        for key, sub in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            yield from flatten_json(sub, path, depth + 1)
    elif isinstance(value, (list, tuple)):
        if not value:
            yield (prefix or "body", "[]")
            return
        for index, sub in enumerate(value):
            path = f"{prefix}.{index}" if prefix else str(index)
            yield from flatten_json(sub, path, depth + 1)
    else:
        yield (prefix or "body", json_scalar_to_text(value))


_CONTENT_DISPOSITION_NAME = re.compile(rb'name="((?:[^"\\]|\\.)*)"', re.IGNORECASE)
_CONTENT_DISPOSITION_FILENAME = re.compile(rb'filename="((?:[^"\\]|\\.)*)"', re.IGNORECASE)


def _multipart_boundary(content_type: str) -> bytes | None:
    for chunk in content_type.split(";")[1:]:
        key, _, raw = chunk.strip().partition("=")
        if key.strip().lower() == "boundary":
            value = raw.strip().strip('"')
            if value:
                return value.encode("latin-1", "replace")
    return None


def iter_multipart(body: bytes, content_type: str) -> Iterator[tuple[str, str | bytes]]:
    """Yield ``(field_name, value)`` for a multipart body, parsed from raw bytes.

    Written by hand rather than with a full parser because this one must survive the
    malformed bodies that show up in production (missing terminator, truncated upload,
    bogus part headers) without raising: an exception here would cost the whole record,
    and malformed uploads are exactly the requests someone will want to look at.
    File parts additionally yield ``<field>.filename``.
    """
    boundary = _multipart_boundary(content_type)
    if not boundary:
        return
    for part in body.split(b"--" + boundary):
        if not part or part in (b"--", b"--\r\n"):
            continue
        head, sep, payload = part.partition(b"\r\n\r\n")
        if not sep:
            continue
        name_match = _CONTENT_DISPOSITION_NAME.search(head)
        if not name_match:
            continue
        name = name_match.group(1).decode("utf-8", "replace")
        payload = payload[:-2] if payload.endswith(b"\r\n") else payload
        file_match = _CONTENT_DISPOSITION_FILENAME.search(head)
        if file_match:
            yield (f"{name}.filename", file_match.group(1).decode("utf-8", "replace"))
        yield (name, payload)


def parse_cookie_header(raw: str) -> Iterator[tuple[str, str]]:
    """Split a Cookie header by hand.

    ``http.cookies.SimpleCookie`` silently discards pairs it considers illegal, and a
    malformed cookie is usually the reason the request is being looked at in the first
    place, so it cannot be used here.
    """
    for chunk in raw.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        name, sep, value = chunk.partition("=")
        name = name.strip()
        if name:
            yield (name, value.strip() if sep else "")


def graphql_params(query: Any, variables: Any, operation_name: Any) -> Iterator[tuple[str, str]]:
    """Yield ``in="graphql"`` attributes for one GraphQL operation.

    Variables keep a ``variables.`` prefix so a dashboard can name one argument
    (``variables.id``) with no ambiguity against ``query`` itself. The document is
    reported verbatim rather than parsed: turning a document into a selection set is
    interpretation, and interpretation belongs downstream, not on the request path.
    """
    if isinstance(query, str) and query:
        yield ("query", query)
    if isinstance(operation_name, str) and operation_name:
        yield ("operationName", operation_name)
    if isinstance(variables, (dict, list)):
        yield from ((f"variables.{path}", text) for path, text in flatten_json(variables, "") if path)
    elif isinstance(variables, str) and variables:
        yield ("variables", variables)


class ParamCollector:
    """Ordered, de-duplicated, bounded accumulator of described inputs.

    De-duplication is on (location, name, value hash) rather than (location, name): a
    repeated parameter carrying a *different* value is the interesting case -- it is
    what makes two identical-looking requests behave differently -- and collapsing it
    to a single entry would hide it. Identical repeats do collapse.
    """

    __slots__ = ("_entries", "_seen", "_max", "truncated")

    def __init__(self, max_params: int = DEFAULT_MAX_PARAMS) -> None:
        self._entries: list[dict[str, Any]] = []
        self._seen: set[tuple[str, str, str]] = set()
        self._max = max_params
        self.truncated = False

    def add(self, name: str, location: str, value: str | bytes) -> None:
        if len(self._entries) >= self._max:
            self.truncated = True
            return
        entry = describe_param(name, location, value)
        key = (location, entry["name"], entry["value_sha256"])
        if key in self._seen:
            return
        self._seen.add(key)
        self._entries.append(entry)

    def add_many(self, pairs, location: str) -> None:
        for name, value in pairs:
            self.add(name, location, value)

    def extend_entries(self, entries) -> None:
        """Merge already-described inputs (from the graphql/websocket helpers)."""
        for entry in entries:
            key = (entry.get("in", ""), entry.get("name", ""), entry.get("value_sha256", ""))
            if key in self._seen or len(self._entries) >= self._max:
                self.truncated = self.truncated or len(self._entries) >= self._max
                continue
            self._seen.add(key)
            self._entries.append(entry)

    @property
    def entries(self) -> list[dict[str, Any]]:
        return self._entries


def collect_query(collector: ParamCollector, query_string: str | bytes) -> None:
    text = _to_text(query_string)
    if not text:
        return
    try:
        pairs = parse_qsl(text, keep_blank_values=True)
    except ValueError:
        collector.add("query_string", "raw", text)
        return
    collector.add_many(pairs, "query")


def collect_headers(collector: ParamCollector, headers) -> None:
    """``headers`` is an iterable of (name, value) with str values."""
    for name, value in headers:
        lowered = name.lower()
        if lowered == "cookie":
            collector.add_many(parse_cookie_header(value), "cookie")
            continue
        if is_described_header(lowered):
            collector.add(lowered, "header", value)


def collect_body(collector: ParamCollector, body: bytes, content_type: str) -> None:
    """Describe the body by content type, sniffing JSON when the type is missing."""
    if not body:
        return
    base = (content_type or "").split(";")[0].strip().lower()
    if base == "application/json" or base.endswith("+json") or (not base and body[:1] in (b"{", b"[")):
        try:
            decoded = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            collector.add("body", "raw", body)
            return
        collector.add_many(flatten_json(decoded), "json")
        if isinstance(decoded, dict) and isinstance(decoded.get("query"), str):
            # A GraphQL call is JSON on the wire; describing it under both locations
            # lets a dashboard be written against either view of the same request.
            collector.add_many(
                graphql_params(decoded.get("query"), decoded.get("variables"), decoded.get("operationName")),
                "graphql",
            )
        return
    if base == "application/x-www-form-urlencoded":
        try:
            pairs = parse_qsl(body.decode("utf-8", "replace"), keep_blank_values=True)
        except ValueError:
            collector.add("body", "raw", body)
            return
        collector.add_many(pairs, "body")
        return
    if base.startswith("multipart/"):
        found = False
        for name, value in iter_multipart(body, content_type):
            found = True
            collector.add(name, "multipart", value)
        if not found:
            collector.add("body", "raw", body)
        return
    if base in ("application/graphql", "application/graphql+json"):
        collector.add("query", "graphql", body)
        return
    collector.add("body", "raw", body)
