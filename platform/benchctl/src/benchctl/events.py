"""Event stream loading and typing.

Consumes exactly what ``platform/collector/openapi.yaml`` produces: ``http_request``,
``trigger``, ``oob`` and ``note`` events, either exported to a JSON file by a runner
or pulled from the collector's ``/v1/runs/{run_id}/events`` endpoint.

Two shapes of file are accepted, because both occur in practice: a bare JSON array
of events, and an ``EventPage``-like object with an ``events`` key (what the
collector returns, possibly concatenated by a runner into ``{"run": ..., "events":
[...]}``).

Anything the scorer needs is normalised here (method upper-cased, ``synthetic``
coerced to bool, params typed), so scoring.py never touches a raw dict. Unknown
event types are kept but ignored downstream; forward compatibility matters because
the SDKs ship separately from this package.

Network access uses ``urllib`` from the standard library on purpose: the scoring
brain must stay installable with two pure-Python dependencies so anyone can rerun
and contest a published score.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

__all__ = [
    "Param",
    "Event",
    "HttpRequestEvent",
    "TriggerEvent",
    "OobEvent",
    "EventStream",
    "load_events",
    "fetch_events",
]


@dataclass(frozen=True)
class Param:
    name: str
    location: str  # the OpenAPI field is `in`, which is a Python keyword
    value_sha256: str | None = None
    value_len: int | None = None
    sample: str | None = None


@dataclass(frozen=True)
class Event:
    type: str
    app: str | None = None
    ts: float | None = None
    synthetic: bool = False
    seq: int | None = None
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class HttpRequestEvent(Event):
    method: str = "GET"
    route: str = "/"
    path: str | None = None
    status: int | None = None
    auth_subject: str | None = None
    params: tuple[Param, ...] = ()


@dataclass(frozen=True)
class TriggerEvent(Event):
    vuln_id: str = ""
    oracle_kind: str | None = None
    evidence: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OobEvent(Event):
    token: str = ""
    channel: str | None = None
    source_ip: str | None = None


@dataclass(frozen=True)
class EventStream:
    """All events of one run, split by type, with synthetic ones already flagged."""

    events: tuple[Event, ...]

    @property
    def requests(self) -> tuple[HttpRequestEvent, ...]:
        return tuple(e for e in self.events if isinstance(e, HttpRequestEvent))

    @property
    def triggers(self) -> tuple[TriggerEvent, ...]:
        return tuple(e for e in self.events if isinstance(e, TriggerEvent))

    @property
    def oob(self) -> tuple[OobEvent, ...]:
        return tuple(e for e in self.events if isinstance(e, OobEvent))

    def scored(self) -> "EventStream":
        """Drop synthetic events -- platform traffic must never credit a tool."""
        return EventStream(tuple(e for e in self.events if not e.synthetic))

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for e in self.events:
            out[e.type] = out.get(e.type, 0) + 1
        out["synthetic"] = sum(1 for e in self.events if e.synthetic)
        out["total"] = len(self.events)
        return dict(sorted(out.items()))


def _as_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    return bool(value)


def _param_from_dict(d: Mapping[str, Any]) -> Param:
    return Param(
        name=str(d.get("name", "")),
        location=str(d.get("in", "query")),
        value_sha256=(d.get("value_sha256") or None),
        value_len=d.get("value_len"),
        sample=d.get("sample"),
    )


def event_from_dict(d: Mapping[str, Any]) -> Event:
    kind = str(d.get("type", ""))
    common = {
        "type": kind,
        "app": d.get("app"),
        "ts": d.get("ts"),
        "synthetic": _as_bool(d.get("synthetic", False)),
        "seq": d.get("seq"),
        "raw": d,
    }
    if kind == "http_request":
        return HttpRequestEvent(
            method=str(d.get("method", "GET")).upper(),
            route=str(d.get("route", d.get("path", "/"))),
            path=d.get("path"),
            status=d.get("status"),
            auth_subject=d.get("auth_subject"),
            params=tuple(_param_from_dict(p) for p in (d.get("params") or []) if isinstance(p, Mapping)),
            **common,
        )
    if kind == "trigger":
        return TriggerEvent(
            vuln_id=str(d.get("vuln_id", "")),
            oracle_kind=d.get("oracle_kind"),
            evidence=d.get("evidence") or {},
            **common,
        )
    if kind == "oob":
        return OobEvent(
            token=str(d.get("token", "")),
            channel=d.get("channel"),
            source_ip=d.get("source_ip"),
            **common,
        )
    return Event(**common)


def events_from_iterable(items: Iterable[Mapping[str, Any]]) -> EventStream:
    return EventStream(tuple(event_from_dict(d) for d in items if isinstance(d, Mapping)))


def load_events(path: Path | str) -> tuple[EventStream, dict[str, Any]]:
    """Load an events export. Returns the stream and any run metadata found."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    meta: dict[str, Any] = {}
    if isinstance(data, list):
        items = data
    elif isinstance(data, Mapping):
        items = data.get("events") or []
        if isinstance(data.get("run"), Mapping):
            meta = dict(data["run"])
        for key in ("run_id", "tool", "tool_version", "profile", "targets"):
            if key in data and key not in meta:
                meta[key] = data[key]
    else:  # pragma: no cover - defensive
        raise ValueError(f"unsupported events file shape: {type(data).__name__}")
    return events_from_iterable(items), meta


def _get_json(url: str, timeout: float) -> Any:
    with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 - fixed scheme, internal network
        return json.loads(resp.read().decode("utf-8"))


def fetch_events(
    collector_url: str, run_id: str, *, page_size: int = 5000, timeout: float = 30.0
) -> tuple[EventStream, dict[str, Any]]:
    """Pull a whole run from the collector, following the ``after_seq`` cursor."""
    base = collector_url.rstrip("/")
    items: list[Mapping[str, Any]] = []
    after: int | None = None
    while True:
        query = {"limit": page_size}
        if after is not None:
            query["after_seq"] = after
        url = f"{base}/v1/runs/{urllib.parse.quote(run_id)}/events?{urllib.parse.urlencode(query)}"
        page = _get_json(url, timeout)
        batch = page.get("events") or []
        items.extend(batch)
        nxt = page.get("next_seq")
        # The collector returns next_seq=null when the cursor is exhausted; guard
        # against a non-advancing cursor too, so a collector bug cannot hang scoring.
        if not batch or nxt is None or (after is not None and nxt <= after):
            break
        after = nxt

    meta: dict[str, Any] = {}
    try:
        runs = _get_json(f"{base}/v1/runs", timeout)
        for run in runs if isinstance(runs, list) else []:
            if run.get("run_id") == run_id:
                meta = dict(run)
                break
    except Exception:  # pragma: no cover - metadata is a nicety, never fatal
        meta = {}
    return events_from_iterable(items), meta
