"""Input enumeration: turn a raw request into the ``params`` array of an ``http_request`` event.

The scorer decides "reached" from the route and "exercised" from these entries, so an
input the SDK fails to enumerate is a vulnerability the tool can never be credited for
finding. Enumeration is therefore deliberately exhaustive and never trusts the app to
have parsed anything: every location is parsed from the raw bytes on the wire.

Values are reported as ``(sha256, length, 256-char sample)`` rather than verbatim. The
sha256 is what lets the scorer compare an observed value against the catalog's
``default_value`` and tell "the tool merely visited this parameter" from "the tool
actually fuzzed it"; the sample exists so a human can audit that verdict.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Iterator
from urllib.parse import parse_qsl

SAMPLE_MAX = 256

# Nesting/size guards. A scanner will happily post a 5 MB deeply nested JSON blob;
# enumeration runs on the request path, so it must be bounded by construction.
JSON_DEPTH_MAX = 16
DEFAULT_MAX_PARAMS = 1024

# Headers a scanner plausibly injects into. Everything ``x-*`` is included because
# custom headers are exactly where header-driven sinks (tenant ids, feature flags,
# debug switches) live in the planted apps.
INJECTABLE_HEADERS = frozenset(
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


def is_injectable_header(name: str) -> bool:
    lowered = name.lower()
    return lowered in INJECTABLE_HEADERS or lowered.startswith("x-")


def sha256_of(value: str | bytes) -> str:
    if isinstance(value, str):
        # surrogatepass keeps lone surrogates (from percent-decoded junk) hashable
        # instead of raising on the request path.
        value = value.encode("utf-8", "surrogatepass")
    return hashlib.sha256(value).hexdigest()


def _to_text(value: str | bytes) -> str:
    if isinstance(value, str):
        return value
    return value.decode("utf-8", "replace")


def describe_param(name: str, location: str, value: str | bytes) -> dict[str, Any]:
    """Build one ``params`` entry. ``value_len`` counts bytes for binary values."""
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

    ``"laptop"`` must hash to sha256(b"laptop") so it matches a catalog
    ``default_value``; a number must hash like its textual form. Hence: strings raw,
    everything else via json.dumps (``true`` / ``null`` / ``1001``).
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
    yielded as leaves so their *name* still shows up as an observable input.
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

    Written by hand rather than with ``python-multipart``/``cgi`` because the parser
    must survive a fuzzer's deliberately malformed parts (missing terminator, bogus
    headers) without raising: a crash here would cost the whole event.
    File parts additionally yield ``<field>.filename``, itself a classic sink input.
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

    ``http.cookies.SimpleCookie`` silently discards pairs it considers illegal, which
    is precisely the shape of an injected cookie payload, so it cannot be used here.
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
    """Yield ``in="graphql"`` entries for one GraphQL operation.

    Variables keep a ``variables.`` prefix so a catalog entry can name a specific
    argument (``param: variables.id``) with no ambiguity against ``query`` itself.
    The document is reported verbatim, not parsed: introspection of the selection set
    is interpretation, and interpretation belongs to the scorer.
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
    """Ordered, de-duplicated, bounded accumulator of ``params`` entries.

    De-duplication is on (location, name, value hash) rather than (location, name):
    a repeated parameter with a *different* value is an HTTP-parameter-pollution
    payload, and collapsing it to one entry would hide the very thing being scored.
    Identical repeats do collapse, which is what the "one entry per name" wording in
    the OpenAPI contract is about.
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
        """Merge pre-built entries (from bench.graphql/bench.websocket helpers)."""
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
        if is_injectable_header(lowered):
            collector.add(lowered, "header", value)


def collect_body(collector: ParamCollector, body: bytes, content_type: str) -> None:
    """Parse the body by content type, with a JSON sniff for content-type-less fuzzing."""
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
            # A GraphQL POST is JSON on the wire; report it under both locations so a
            # catalog entry can be written against either view of the same request.
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
