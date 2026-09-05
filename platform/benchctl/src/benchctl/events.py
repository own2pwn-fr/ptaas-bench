"""Event stream loading and typing.

Consumes what ``platform/collector/openapi.yaml`` produces: ``http_request``,
``signal`` (a planted sink firing), ``oob`` and ``note`` events, either exported to a
JSON file by a runner or pulled from the collector's ``/v1/runs/{run_id}/events``
endpoint.

Wire vocabulary vs ours. The words ``trigger``, ``evidence`` and ``oracle_kind``
were forbidden strings sitting as literals in shipped target source, so the SDKs
renamed them: the event type is ``signal``, its payload is ``attributes``, and
``oracle_kind`` is gone -- the catalog already declares ``oracle.kind`` and the SDK
was duplicating authoritative data, so the kind is read from the catalog and never
from an event. Both the current and the legacy spellings are accepted here, because
a benchmark must be able to re-score an archived run years later. Everything past
this module keeps the platform's own vocabulary (TRIGGER as the third scoring axis,
``oracle``, ``catalog``): that lives on our side of the fence, and only what ships
into a target container is constrained.

Ordering is never load-bearing. A correlation may arrive after the sinkhole
observation it explains, because SDKs dispatch correlations immediately over a
separate connection. Everything downstream joins on content (signal, token,
destination host, request id), never on arrival order or event index.

``synthetic`` is decided by the SDK from the socket peer address alone. Any
``client_ip`` in an event is descriptive and untrusted -- it can be forged through a
forwarded header, and a tool able to mark its own traffic synthetic would erase
itself from the scores -- so nothing in this package derives a scoring decision
from it.

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
    "SIGNAL_EVENT_TYPES",
    "Param",
    "Event",
    "HttpRequestEvent",
    "TriggerEvent",
    "OobEvent",
    "EventStream",
    "load_events",
    "fetch_events",
]


# Wire type names for "a planted sink fired". "trigger" is the legacy spelling and
# is kept forever: archived runs must stay re-scorable.
SIGNAL_EVENT_TYPES = ("signal", "trigger")


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
    """A planted sink fired.

    Wire type ``signal`` (legacy alias ``trigger``).

    ``signal`` is the opaque, metric-shaped identifier the target emits
    (``shop.catalog.query.plan_anomaly``); it is the normal case. ``vuln_id`` is
    still accepted because platform-side components (the canary, a replayed
    archive) may carry it, but a *target* must never emit one -- a tool that
    compromises the host has to find an anomaly counter, not a graded exercise.
    """

    vuln_id: str = ""
    signal: str | None = None
    # Wire name: `attributes`. The oracle kind is deliberately NOT carried here --
    # the catalog is authoritative for it.
    evidence: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OobEvent(Event):
    """A callback observed by the egress sinkhole.

    The sinkhole is the DNS resolver and blackhole for the whole target network, so
    it also sees callbacks aimed at the tool's own collaborator domain. Without
    that, every blind flaw would score as missed by every tool -- a property of our
    sealed topology, not of the tools. The price is that attribution is no longer
    a token lookup, so the event says how it was attributed:

    ``token``            a token from our own zone (definitive).
    ``signal``           the sink registered {signal, destination_host, route,
                         param, request_id} with the collector before making the
                         outbound fetch and the sinkhole matched the observed
                         lookup against it (definitive).
    weak fallback        only the originating container and a time window matched.
                         Arrives flagged, and this scorer keeps it out of the
                         headline recall.

    ``confidence``/``low_confidence``/``attribution`` carry that flag; all three
    spellings are accepted because the sinkhole and the collector are separate
    components and this scorer must not break when one of them is upgraded first.
    """

    token: str = ""
    signal: str | None = None
    vuln_id: str | None = None
    channel: str | None = None
    source_ip: str | None = None
    attribution: str | None = None
    confidence: str | None = None
    low_confidence: bool = False
    destination_host: str | None = None
    request_id: str | None = None
    container: str | None = None
    route: str | None = None
    method: str | None = None


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
    if kind in SIGNAL_EVENT_TYPES:
        # Normalise the legacy alias so counts and filters have one spelling.
        kind = SIGNAL_EVENT_TYPES[0]
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
    if kind in SIGNAL_EVENT_TYPES:
        return TriggerEvent(
            vuln_id=str(d.get("vuln_id") or ""),
            signal=(d.get("signal") or None),
            evidence=d.get("attributes") or d.get("evidence") or {},
            **common,
        )
    if kind == "oob":
        return OobEvent(
            token=str(d.get("token") or ""),
            signal=(d.get("signal") or None),
            vuln_id=(d.get("vuln_id") or None),
            channel=d.get("channel"),
            source_ip=d.get("source_ip"),
            attribution=(d.get("attribution") or d.get("attribution_kind") or None),
            confidence=(d.get("confidence") or None),
            low_confidence=_as_bool(d.get("low_confidence", False)),
            destination_host=(d.get("destination_host") or d.get("host") or None),
            request_id=(d.get("request_id") or None),
            container=(d.get("container") or None),
            route=(d.get("route") or None),
            method=(str(d["method"]).upper() if d.get("method") else None),
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
