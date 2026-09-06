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

The run record is parsed here too (:func:`normalize_run_record`). It carries the
container/address map captured by the orchestrator at run open, the image digest
actually running for each target, and the reset state digest read before and after
the run -- everything needed to re-run a published number from its own record.

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
    "normalize_run_record",
    "address_index",
    "host_app_index",
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
    # Optional: the vhost that served the request. Today's SDKs do not report it,
    # so crawl coverage collapses the host and says so; when they do, coverage of a
    # multi-vhost target becomes exact without any further change here.
    host: str | None = None
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

    ``source_match`` is informational only. The resolver reports a host match as
    high confidence unconditionally and records separately whether the callback's
    source address also agreed; using that agreement as a downgrade would publish
    every genuine match as second-rate, because a target's outbound address differs
    from the one its correlation hint was registered from. So it is carried through
    to the report as a fact about the observation, never as a reason to discount it.
    """

    token: str = ""
    signal: str | None = None
    vuln_id: str | None = None
    channel: str | None = None
    source_ip: str | None = None
    attribution: str | None = None
    # The app the resolver itself attributed the callback to, when it says so.
    attributed_app: str | None = None
    confidence: str | None = None
    source_match: bool | None = None
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



# Where the orchestrator's container map may live on the run record. Several
# spellings are accepted because the collector and the orchestrator ship
# separately; a scorer that breaks when one of them is upgraded first is useless.
# `addresses` and `target_topology` are what the harness actually writes; the rest
# are older spellings kept so an archived run stays re-scorable.
_CONTAINER_MAP_KEYS = ("addresses", "target_topology", "containers", "container_map",
                       "target_containers", "targets_map")
_IMAGE_MAP_KEYS = ("images", "image_digests")
_RESET_MAP_KEYS = ("reset", "reset_digests", "state_digests")


def _as_str(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None


def normalize_run_record(meta: Mapping[str, Any] | None) -> dict[str, Any]:
    """Normalise the run record into the shape the score document publishes.

    Three things matter downstream, and all three come from the orchestrator rather
    than from any target, so a compromised target cannot forge them:

    * ``containers`` -- ``{app: {service, container_id, addresses: [...]}}`` captured
      at run open. Every target is dual-homed (a correlation hint is registered over
      bench-internal and carries a 10.77.0.x address, while the callback it explains
      leaves over bench-public as 10.88.0.x -- same container, no octet relationship),
      so an address is only ever resolved to an app by lookup in this map, never by
      arithmetic on the address. The map is true for one run only, because container
      addresses are reassigned between runs.
    * ``images`` -- the image digest actually running for each target.
    * ``reset`` -- the state digest read before and after the run. They must be equal:
      a run that did not put the target back to its seeded state means whatever ran
      next was measured against a different application.
    """
    meta = meta or {}
    containers: dict[str, dict[str, Any]] = {}
    raw_map: Mapping[str, Any] = {}
    for key in _CONTAINER_MAP_KEYS:
        candidate = meta.get(key)
        if isinstance(candidate, Mapping) and candidate:
            raw_map = candidate
            break

    for app, entry in raw_map.items():
        if not isinstance(entry, Mapping):
            continue
        # An app is several containers on several networks, so an address list is
        # flattened from every service and every network it is attached to. The
        # per-service detail is kept: which image was actually running is provenance,
        # and a callback's source address must resolve to the app whatever service
        # inside it made the call.
        services = [x for x in (entry.get("services") or []) if isinstance(x, Mapping)]
        addresses: list[str] = [str(a) for a in (entry.get("addresses") or entry.get("ips") or [])]
        for service in services:
            for address in service.get("addresses") or ():
                if isinstance(address, Mapping):
                    ip = address.get("ip") or address.get("address")
                    if ip:
                        addresses.append(str(ip))
                elif address:
                    addresses.append(str(address))
        containers[str(app)] = {
            "service": _as_str(entry.get("service")) or (
                _as_str(services[0].get("service")) if services else None),
            "container_id": _as_str(entry.get("container_id") or entry.get("id")) or (
                _as_str(services[0].get("container_id")) if services else None),
            "addresses": list(dict.fromkeys(addresses)),
            "image_digest": _as_str(entry.get("image_digest") or entry.get("image")) or (
                _as_str(services[0].get("image_digest")) if services else None),
            "services": [
                {
                    "service": _as_str(x.get("service") or x.get("container_name")),
                    "container_id": _as_str(x.get("container_id")),
                    "image_digest": _as_str(x.get("image_digest") or x.get("image")),
                }
                for x in services
            ],
            "reset_digest_before": _as_str(entry.get("reset_digest_before")),
            "reset_digest_after": _as_str(entry.get("reset_digest_after")),
        }

    images: dict[str, str] = {}
    for key in _IMAGE_MAP_KEYS:
        candidate = meta.get(key)
        if isinstance(candidate, Mapping):
            images.update({str(k): str(v) for k, v in candidate.items() if v})
    for app, entry in containers.items():
        if entry["image_digest"]:
            images.setdefault(app, entry["image_digest"])

    reset: dict[str, dict[str, Any]] = {}

    def _record(app: str, before: Any, after: Any, ok: Any = None) -> None:
        before, after = _as_str(before), _as_str(after)
        if before is None and after is None and ok is None:
            return
        reset[app] = {
            "before": before,
            "after": after,
            # None, not False, when one side is missing: unknown is not a mismatch.
            "match": (before == after) if (before and after) else None,
            "ok": None if ok is None else bool(ok),
        }

    for key in _RESET_MAP_KEYS:
        candidate = meta.get(key)
        if isinstance(candidate, (list, tuple)):
            # What the harness writes: one entry per app, comparing the digest it
            # read after resetting against the seeded reference. Same question as
            # before/after -- did the target come back to its seeded state -- asked
            # in the other direction.
            for entry in candidate:
                if isinstance(entry, Mapping) and entry.get("app"):
                    _record(str(entry["app"]),
                            entry.get("reference_digest") or entry.get("before"),
                            entry.get("state_digest") or entry.get("after"),
                            ok=entry.get("ok"))
            continue
        if not isinstance(candidate, Mapping):
            continue
        if any(k in candidate for k in ("before", "after")):
            _record("*", candidate.get("before"), candidate.get("after"))
        else:
            for app, entry in candidate.items():
                if isinstance(entry, Mapping):
                    _record(str(app), entry.get("before"), entry.get("after"),
                            ok=entry.get("ok"))
    _record("*", meta.get("reset_digest_before"), meta.get("reset_digest_after"))
    for app, entry in containers.items():
        _record(app, entry["reset_digest_before"], entry["reset_digest_after"])

    matches = [r["match"] for r in reset.values() if r["match"] is not None]
    matches += [r["ok"] for r in reset.values() if r["match"] is None and r["ok"] is not None]

    raw_mode = meta.get("scan_mode")
    scan_mode: dict[str, Any] | None = None
    if isinstance(raw_mode, Mapping):
        scan_mode = {
            "mode": _as_str(raw_mode.get("mode")),
            "active": (None if raw_mode.get("active") is None else bool(raw_mode["active"])),
            "reason": _as_str(raw_mode.get("reason")),
        }
    elif raw_mode:
        scan_mode = {"mode": _as_str(raw_mode), "active": None, "reason": None}

    caveats = [str(c) for c in (meta.get("caveats") or []) if c]

    requests = meta.get("requests")
    requests_block = None
    if isinstance(requests, Mapping):
        requests_block = {
            "total": requests.get("total"),
            "by_app": dict(requests.get("by_app") or {}),
            "counted_by": _as_str(requests.get("counted_by")),
        }
    elif meta.get("requests_observed") is not None:
        requests_block = {"total": meta["requests_observed"], "by_app": {}, "counted_by": None}

    return {
        "containers": containers,
        "container_map_available": bool(containers),
        "images": dict(sorted(images.items())),
        # The tool's own image, which is not a target image and must not be mixed in.
        "tool_image": _as_str(meta.get("image")),
        "tool_image_digest": _as_str(meta.get("image_digest")),
        "reset_digests": dict(sorted(reset.items())),
        "reset_consistent": (all(matches) if matches else None),
        "scan_mode": scan_mode,
        "caveats": caveats,
        "requests": requests_block,
    }


def address_index(containers: Mapping[str, Mapping[str, Any]]) -> dict[str, str]:
    """Every address / container id / service name of a target -> its app key.

    Deterministic lookup, one entry per address on every network the container is
    attached to. Nothing here parses or compares address octets: two addresses of
    the same dual-homed container have no numeric relationship, so any arithmetic
    would be wrong for every target we ship.
    """
    index: dict[str, str] = {}
    for app, entry in containers.items():
        for address in entry.get("addresses") or ():
            index[str(address)] = app
        for key in ("container_id", "service"):
            value = entry.get(key)
            if value:
                index[str(value)] = app
        index.setdefault(app, app)
    return index


def _as_text(value: Any, *keys: str) -> str | None:
    """Coerce a wire field to text, tolerating a structured object.

    Emitters disagree about whether a field is a scalar or an object -- the
    resolver reports ``attribution`` as ``{"app": null, "mode": "unattributed"}``
    while older streams send the bare string ``"unattributed"`` -- and there are six
    emitters in the corpus. A scorer that raises on the shape it did not expect
    makes every affected observation unscoreable and looks like a crash rather than
    a contract disagreement, so every scalar-ish field is read through here.
    """
    if value is None:
        return None
    if isinstance(value, Mapping):
        for key in (*keys, "value", "name", "id"):
            if value.get(key) not in (None, ""):
                return str(value[key])
        return None
    if isinstance(value, (list, tuple)):
        return _as_text(value[0], *keys) if value else None
    text = str(value).strip()
    return text or None


def host_app_index(stream: "EventStream") -> dict[str, str]:
    """Host header -> app, observed on the wire.

    A tool's findings carry the hostname it was pointed at (``press01:8000``), which
    is generated per deployment from BENCH_DEPLOY_SEED and appears in no catalog or
    inventory. The collector, however, saw both the Host header and the app that
    served it, so the mapping is a fact of the run rather than a convention: without
    it a scanner's correct detection cannot be matched to the flaw it found, which
    is the difference between a true positive and a published false one.

    A host serving two apps is dropped rather than guessed at.
    """
    seen: dict[str, set[str]] = {}
    for ev in stream.events:
        if not isinstance(ev, HttpRequestEvent) or ev.synthetic or not ev.app or not ev.host:
            continue
        host = str(ev.host).strip().lower()
        for key in {host, host.split(":", 1)[0]}:
            if key:
                seen.setdefault(key, set()).add(ev.app)
    return {host: next(iter(apps)) for host, apps in seen.items() if len(apps) == 1}


def _as_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    return bool(value)


def _param_from_dict(d: Mapping[str, Any]) -> Param:
    return Param(
        name=_as_text(d.get("name")) or "",
        location=_as_text(d.get("in")) or "query",
        value_sha256=_as_text(d.get("value_sha256")),
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
            route=_as_text(d.get("route"), "template") or _as_text(d.get("path")) or "/",
            host=_as_text(d.get("host") or d.get("vhost"), "host", "name"),
            path=_as_text(d.get("path")),
            status=d.get("status"),
            auth_subject=d.get("auth_subject"),
            params=tuple(_param_from_dict(p) for p in (d.get("params") or []) if isinstance(p, Mapping)),
            **common,
        )
    if kind in SIGNAL_EVENT_TYPES:
        return TriggerEvent(
            vuln_id=_as_text(d.get("vuln_id")) or "",
            signal=_as_text(d.get("signal"), "name"),
            evidence=d.get("attributes") or d.get("evidence") or {},
            **common,
        )
    if kind == "oob":
        attribution = d.get("attribution") or d.get("attribution_kind")
        # The resolver's structured attribution also names the app it resolved, and
        # that is a fact from the platform side worth keeping rather than re-deriving.
        attributed_app = (
            _as_text(attribution.get("app")) if isinstance(attribution, Mapping) else None
        )
        return OobEvent(
            token=_as_text(d.get("token")) or "",
            signal=_as_text(d.get("signal")),
            vuln_id=_as_text(d.get("vuln_id")),
            channel=_as_text(d.get("channel")),
            source_ip=_as_text(d.get("source_ip"), "address", "ip"),
            attribution=_as_text(attribution, "mode", "kind"),
            attributed_app=attributed_app,
            confidence=_as_text(d.get("confidence"), "level"),
            source_match=(None if d.get("source_match") is None
                          else _as_bool(d.get("source_match"))),
            low_confidence=_as_bool(d.get("low_confidence", False)),
            destination_host=_as_text(d.get("destination_host") or d.get("host"), "host"),
            request_id=_as_text(d.get("request_id")),
            container=_as_text(d.get("container"), "container_id"),
            route=_as_text(d.get("route"), "path"),
            method=(str(_as_text(d.get("method")) or "").upper() or None),
            **common,
        )
    return Event(**common)


def events_from_iterable(items: Iterable[Mapping[str, Any]]) -> EventStream:
    return EventStream(tuple(event_from_dict(d) for d in items if isinstance(d, Mapping)))


def load_events(path: Path | str) -> tuple[EventStream, dict[str, Any]]:
    """Load an events export. Returns the stream and any run metadata found."""
    text = Path(path).read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # The harness exports one event per line. Both shapes are read here rather
        # than agreed by convention: the writer and the reader were built by
        # different hands against the same contract, and the first live active run
        # failed at this exact boundary with a JSON parse error that says nothing
        # about the disagreement it stands for.
        events = [json.loads(line) for line in text.splitlines() if line.strip()]
        if not events:
            raise
        data = events if not isinstance(events[0], dict) or "events" not in events[0] else events[0]
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
