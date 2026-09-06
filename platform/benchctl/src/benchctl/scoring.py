"""Reach / exercise / trigger scoring and aggregation.

Three independent booleans per planted vulnerability. Independent is the point:
they answer three different questions about a tool, and averaging them together
would hide exactly the failure this benchmark exists to expose (tools that crawl a
lot, fuzz a little and exploit almost nothing).

REACH -- "did the tool ever touch the vulnerable endpoint?"
    A non-synthetic ``http_request`` event with the same app, the same method and a
    route that normalises equal to the catalog entrypoint
    (:func:`benchctl.routes.routes_equal`). Method comparison is exact: a HEAD does
    not credit reach on a GET entrypoint, because a HEAD cannot observe the flaw.

EXERCISE -- "did the tool put its own value into the injection point?"
    One of the reaching requests carried the catalog ``param`` in the declared
    ``param_in``, with a value whose sha256 differs from sha256(default_value).
    Comparing hashes rather than samples is what lets the collector store nothing
    sensitive while still separating a crawler (replays the value the app itself
    uses) from a scanner (sends something else).
      * ``default_value: null`` -> any non-empty value counts.
      * ``param: null`` -> EXERCISE is *not applicable*. It is reported as ``null``,
        never ``false``, and is excluded from every denominator, so endpoints with
        no injection point (exposed .git, TRACE enabled) cannot drag an average down.
      * ``param_in`` is matched through a small equivalence class for body-ish
        locations (``body``/``json``/``multipart``/``raw``). The five language SDKs
        genuinely disagree on how they label a parsed request body, and that
        disagreement is an instrumentation artefact, not a property of the tool.
        Every other location (query, path, header, cookie, graphql, websocket) must
        match exactly.

TRIGGER -- "did the tool actually exploit it?"
    A non-synthetic ``trigger`` event that maps to the vulnerability, or an
    out-of-band callback attributed to it.

    Trigger events are matched on ``oracle.signal`` first: targets emit an opaque,
    metric-shaped signal (``shop.catalog.query.plan_anomaly``) and never a catalog
    id, so that a tool which compromises a target and reads the source finds an
    ordinary anomaly counter rather than a graded exercise. The signal -> id index
    is built from the catalog, which also enforces signal uniqueness. A ``vuln_id``
    is still honoured for platform-side emitters and archived runs. A signal that
    no catalog entry claims is reported as a warning: it means a target is emitting
    something the catalog forgot, i.e. an uncredited planted flaw.

    Out-of-band callbacks are attributed three ways, and they are NOT equivalent:

      token              the callback carried a token from our own zone.
      signal-correlation the sink registered {signal, destination_host, route,
                         param, request_id} with the collector before making the
                         outbound fetch, and the sinkhole matched the observed
                         lookup against that registration.
      container-window   only the originating container and a time window matched.

    The first two are proof and credit TRIGGER. The third is a guess, arrives
    flagged as low confidence, and is counted in ``low_confidence_triggers`` and in
    the parallel ``trigger_any`` axis -- never in the headline ``trigger`` recall.
    Both numbers appear in every breakdown, so the difference is always visible.
    Silently promoting a weak attribution to a proven exploit is precisely the
    failure mode this benchmark exists to expose in other people's numbers.

    Attribution never depends on the order events arrive in: a correlation record
    can land in the export after the sinkhole observation it explains, so the join
    is on content (token, signal, request id, destination host).

    Why a sinkhole at all: it is the DNS resolver and blackhole for the whole
    target network, so it also captures callbacks aimed at the tool's own
    collaborator domain (burpcollaborator.net, oast.fun, an agent's own host).
    Without that, every blind SSRF/XXE/command-injection would score as missed for
    every tool -- a property of our sealed topology, not of the tools.

Consistency: TRIGGER implies REACH. If a vulnerability triggered without a
matching request event, the report shows reach = true anyway and emits a
``trigger-without-reach`` warning, because the only possible explanation is an SDK
under-reporting requests -- and silently scoring reach = false there would
under-report the tool for a platform bug. The analogous
``trigger-without-exercise`` case is reported as a warning too, but does *not*
promote exercise: exercise is a claim about a specific parameter, and inferring it
from a trigger would let an out-of-band oracle manufacture a fuzzing claim we never
observed. Only a high-confidence trigger promotes reach: a container-window guess
is not evidence that the endpoint was ever touched.

CRAWL COVERAGE -- reach is measured over planted vulnerabilities, which is a biased
denominator: those are the pages we made attractive. When targets publish a route
inventory (targets/<app>/routes.yaml), ``metrics.crawl`` additionally reports
coverage over the whole declared surface, planted and safe routes alike. Both
numbers are published; neither replaces the other.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping, Sequence

from .catalog import Catalog, Vuln
from .events import (
    EventStream,
    HttpRequestEvent,
    OobEvent,
    address_index,
    normalize_run_record,
)
from .inventory import RouteInventory, normalize_host
from .routes import normalize_route, routes_equal

__all__ = [
    "SCORE_SCHEMA_VERSION",
    "AXES",
    "correlation_index",
    "Attribution",
    "Outcome",
    "sha256_of",
    "param_in_matches",
    "attribute_oob",
    "attribute_oob_events",
    "score_vuln",
    "score_run",
]

# 1.1.0 added the trigger_any axis, low_confidence_triggers and metrics.crawl.
# 1.2.0 added the scope block, the out-of-catalog findings verdict and
#       findings.precision_basis.
SCORE_SCHEMA_VERSION = "1.2.0"

# The four reported axes. `trigger` is the headline (proof only); `trigger_any`
# additionally counts low-confidence out-of-band attributions.
AXES = ("reach", "exercise", "trigger", "trigger_any")

# Markers, any of which demote an out-of-band attribution to a guess. Several
# spellings are accepted because the sinkhole and the collector ship separately and
# this scorer must not silently mis-grade when one of them is upgraded first.
_WEAK_ATTRIBUTIONS = frozenset({
    "container", "container-window", "container_window", "time-window", "time_window",
    "window", "heuristic", "fallback", "weak", "low", "low-confidence",
})
_WEAK_CONFIDENCE = frozenset({"low", "weak", "guess"})

# Body-ish parameter locations, treated as interchangeable. See module docstring.
_BODY_LOCATIONS = frozenset({"body", "json", "multipart", "raw"})

_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


def sha256_of(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def param_in_matches(catalog_in: str, event_in: str) -> bool:
    a, b = (catalog_in or "query").lower(), (event_in or "query").lower()
    if a == b:
        return True
    return a in _BODY_LOCATIONS and b in _BODY_LOCATIONS


@dataclass(frozen=True)
class Attribution:
    """How one out-of-band callback was tied back to a planted vulnerability."""

    vuln_id: str | None
    kind: str  # token | signal-correlation | declared-id | container-window | unattributed
    confidence: str  # "high" | "low"
    app: str | None = None  # resolved from the run's container/address map
    # Informational: did the callback's source address also agree with the
    # registration? Never a downgrade -- see _is_weak.
    source_match: bool | None = None
    channel: str | None = None
    destination_host: str | None = None
    request_id: str | None = None
    container: str | None = None
    ts: float | None = None

    @property
    def is_proof(self) -> bool:
        return self.vuln_id is not None and self.confidence == "high"

    def as_dict(self) -> dict[str, Any]:
        return {
            "vuln_id": self.vuln_id,
            "kind": self.kind,
            "confidence": self.confidence,
            "app": self.app,
            "source_match": self.source_match,
            "channel": self.channel,
            "destination_host": self.destination_host,
            "request_id": self.request_id,
            "container": self.container,
            "ts": self.ts,
        }


@dataclass
class Outcome:
    """Per-vulnerability verdict plus the evidence that produced it."""

    vuln_id: str
    reach: bool = False
    exercise: bool | None = None  # None = not applicable (no param declared)
    trigger: bool = False  # headline: proof only
    trigger_low_confidence: bool = False  # attributed by container + time window
    reach_events: int = 0
    trigger_events: int = 0
    reach_inferred: bool = False  # reach set from a trigger, no request seen
    first_reach_ts: float | None = None
    first_trigger_ts: float | None = None
    exercise_sample: str | None = None
    trigger_source: str | None = None  # "signal" | "vuln_id" | "oob:<kind>"
    attributions: list[dict[str, Any]] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def trigger_any(self) -> bool:
        """Trigger including low-confidence out-of-band attributions."""
        return self.trigger or self.trigger_low_confidence

    def as_dict(self) -> dict[str, Any]:
        return {
            "vuln_id": self.vuln_id,
            "reach": self.reach,
            "exercise": self.exercise,
            "trigger": self.trigger,
            "trigger_any": self.trigger_any,
            "trigger_low_confidence": self.trigger_low_confidence,
            "reach_events": self.reach_events,
            "trigger_events": self.trigger_events,
            "reach_inferred": self.reach_inferred,
            "first_reach_ts": self.first_reach_ts,
            "first_trigger_ts": self.first_trigger_ts,
            "exercise_sample": self.exercise_sample,
            "trigger_source": self.trigger_source,
            "attributions": self.attributions,
            "evidence": self.evidence,
        }


def _is_weak(ev: OobEvent) -> bool:
    """Only an explicit flag from the resolver demotes an attribution.

    Deliberately does NOT look at ``source_match``: the resolver reports a host
    match as high confidence unconditionally, because a target's outbound address
    legitimately differs from the address its correlation hint was registered from
    (targets are dual-homed). Treating address disagreement as a downgrade would
    publish every genuine match as second-rate. It travels to the report as
    information instead.
    """
    # The resolver reports attribution as a structured object ({app, mode}); older
    # streams and the test fixtures carry a bare string. Both are read, because a
    # scorer that raises on one shape would have made every out-of-band callback
    # unscoreable while looking like a crash rather than a contract disagreement.
    raw = ev.attribution
    if isinstance(raw, dict):
        raw = raw.get("mode") or raw.get("kind") or ""
    marker = (raw or "").strip().lower()
    return (
        ev.low_confidence
        or (ev.confidence or "").strip().lower() in _WEAK_CONFIDENCE
        or marker in _WEAK_ATTRIBUTIONS
    )


def _norm_host(host: str | None) -> str:
    return (host or "").strip().rstrip(".").lower()


def correlation_index(stream: EventStream) -> tuple[dict[str, str], dict[str, str]]:
    """Content join keys for out-of-band correlations: host -> signal, request -> signal.

    A sink registers ``{signal, destination_host, route, param, request_id}`` with
    the collector immediately before its outbound fetch, over a separate connection,
    so that record can land in the export *after* the sinkhole observation it
    explains. A registration is not itself evidence -- it describes a payload, not
    an effect -- so it must not arrive as a sink-fired ``signal`` event, and this
    index only ever supplies a join key. Nothing here depends on order: every event in the stream is scanned,
    whatever its type, and the join is on content. Reading ``raw`` rather than a
    typed field is deliberate -- a future correlation event type must work without
    a change here.
    """
    by_host: dict[str, str] = {}
    by_request: dict[str, str] = {}
    for ev in stream.events:
        raw = ev.raw or {}
        signal = raw.get("signal")
        if not signal:
            continue
        host = _norm_host(raw.get("destination_host") or raw.get("host"))
        if host:
            by_host.setdefault(host, str(signal))
        request_id = raw.get("request_id")
        if request_id:
            by_request.setdefault(str(request_id), str(signal))
    return by_host, by_request


def _signal_for_host(host: str | None, by_host: Mapping[str, str]) -> str | None:
    """Exact host match, then the registered host as a DNS suffix of the observed one.

    Tools routinely prepend labels to their collaborator hostname (one per probe),
    so the lookup a sinkhole sees is often a subdomain of what the sink registered.
    """
    key = _norm_host(host)
    if not key:
        return None
    if key in by_host:
        return by_host[key]
    for registered, signal in by_host.items():
        if key.endswith("." + registered) or registered.endswith("." + key):
            return signal
    return None


def _resolve_source_app(
    ev: OobEvent, catalog: Catalog, addresses: Mapping[str, str] | None
) -> str | None:
    """Which target made this callback, resolved deterministically or not at all.

    The orchestrator captures every container's addresses on every network at run
    open, so a source address maps to an app by lookup. That is the only address
    reasoning allowed here: targets are dual-homed and the two addresses of one
    container have no numeric relationship, so inferring an app from an address
    range (or from an octet) is wrong for every target we ship.

    Without a map, only an explicit, exact app or container name carried by the
    event itself is honoured -- that is the platform stating a fact, not us
    inferring one.
    """
    known_apps = {v.app for v in catalog.vulns}
    # The resolver may have done this already and said so; its answer wins, since it
    # saw the connection and we only see the record of it.
    if ev.attributed_app and ev.attributed_app in known_apps:
        return ev.attributed_app
    if addresses:
        for key in (ev.attributed_app, ev.source_ip, ev.container, ev.app):
            if key and key in addresses:
                return addresses[key]
        return None
    for key in (ev.container, ev.app):
        if key and key in known_apps:
            return key
    return None


def attribute_oob(
    ev: OobEvent,
    catalog: Catalog,
    correlations: tuple[Mapping[str, str], Mapping[str, str]] | None = None,
    addresses: Mapping[str, str] | None = None,
) -> Attribution:
    """Tie one callback to a vulnerability, and say how strong the tie is.

    Order matters: a token from our own zone is definitive whatever else the event
    says, then a signal registered by the sink before its outbound fetch, then an
    explicit id (platform-side emitters only), then -- when the sinkhole could only
    match a container and a time window -- a guess, which is kept out of the
    headline recall. An event carrying an explicit low-confidence flag stays low
    confidence even when it also names a signal: the flag is the sinkhole telling
    us it is not sure, and this scorer never overrules it upwards.
    """
    weak = _is_weak(ev)
    source_app = _resolve_source_app(ev, catalog, addresses)
    common = {
        "app": source_app,
        "source_match": ev.source_match,
        "channel": ev.channel,
        "destination_host": ev.destination_host,
        "request_id": ev.request_id,
        "container": ev.container,
        "ts": ev.ts,
    }

    if ev.token and ev.token in catalog.by_token:
        return Attribution(catalog.by_token[ev.token].id, "token",
                           "low" if weak else "high", **common)
    if ev.signal and ev.signal in catalog.by_signal:
        return Attribution(catalog.by_signal[ev.signal].id, "signal-correlation",
                           "low" if weak else "high", **common)
    if ev.vuln_id and ev.vuln_id in catalog.by_id:
        return Attribution(catalog.by_id[ev.vuln_id].id, "declared-id",
                           "low" if weak else "high", **common)

    # The sinkhole saw the callback but the matching registration travelled
    # separately; join the two on content (request id first, then destination host).
    by_host, by_request = correlations or ({}, {})
    signal = by_request.get(str(ev.request_id)) if ev.request_id else None
    signal = signal or _signal_for_host(ev.destination_host, by_host)
    if signal and signal in catalog.by_signal:
        return Attribution(catalog.by_signal[signal].id, "signal-correlation",
                           "low" if weak else "high", **common)

    # Last resort: the sinkhole saw a callback and could tie it to a container and a
    # time window, nothing more. With the run's address map the container -> app step
    # is exact, so the only remaining weakness is the window -- which is why this
    # tier stays low confidence and out of the headline recall. Still requires
    # exactly one candidate: otherwise the guess would pick a flaw at random.
    if source_app or ev.route:
        candidates = [
            v for v in catalog.vulns
            if v.oracle.kind == "oob"
            and (not source_app or v.app == source_app)
            and (not ev.route or routes_equal(v.entrypoint.path, ev.route))
            and (not ev.method or v.entrypoint.method == ev.method)
        ]
        if len(candidates) == 1:
            return Attribution(candidates[0].id, "container-window", "low", **common)
    return Attribution(None, "unattributed", "low", **common)


def attribute_oob_events(
    stream: EventStream,
    catalog: Catalog,
    addresses: Mapping[str, str] | None = None,
) -> tuple[dict[str, list[Attribution]], list[Attribution]]:
    """Attribute every callback once; returns (by vuln id, unattributed)."""
    by_vuln: dict[str, list[Attribution]] = {}
    orphans: list[Attribution] = []
    correlations = correlation_index(stream)
    for ev in stream.oob:
        att = attribute_oob(ev, catalog, correlations, addresses)
        if att.vuln_id is None:
            orphans.append(att)
        else:
            by_vuln.setdefault(att.vuln_id, []).append(att)
    return by_vuln, orphans


def _request_reaches(vuln: Vuln, ev: HttpRequestEvent) -> bool:
    if vuln.app and ev.app and vuln.app != ev.app:
        return False
    if vuln.entrypoint.method.upper() != ev.method.upper():
        return False
    return routes_equal(vuln.entrypoint.path, ev.route)


def _param_is_exercised(vuln: Vuln, ev: HttpRequestEvent) -> tuple[bool, str | None]:
    """Did this request carry a non-default value in the catalog parameter?"""
    want_name = (vuln.entrypoint.param or "").lower()
    default = vuln.entrypoint.default_value
    default_sha = sha256_of(default) if default is not None else None

    for p in ev.params:
        if p.name.lower() != want_name:
            continue
        if not param_in_matches(vuln.entrypoint.param_in, p.location):
            continue
        sha = (p.value_sha256 or "").lower() or None
        if sha is None and p.sample is not None and p.value_len == len(p.sample):
            # The SDK may omit the hash but ship an untruncated sample; recompute
            # rather than lose the observation.
            sha = sha256_of(p.sample)
        if default_sha is None:
            # No declared default: any non-empty value proves the tool supplied one.
            non_empty = (
                (p.value_len is not None and p.value_len > 0)
                or (sha is not None and sha != _EMPTY_SHA256)
                or bool(p.sample)
            )
            if non_empty:
                return True, p.sample
            continue
        if sha is not None and sha != default_sha:
            return True, p.sample
        if sha is None and p.sample is not None and p.sample != default:
            # Truncated sample that already differs from the default is conclusive.
            return True, p.sample
    return False, None


def score_vuln(
    vuln: Vuln, stream: EventStream, *, oob: Sequence[Attribution] = ()
) -> Outcome:
    """Score one vulnerability against an already synthetic-filtered stream.

    ``oob`` is this vulnerability's share of the callback attributions computed by
    :func:`attribute_oob_events`; attribution needs the whole catalog, so it is done
    once by :func:`score_run` rather than per vulnerability here.
    """
    out = Outcome(vuln_id=vuln.id)
    if vuln.has_param:
        out.exercise = False

    for ev in stream.requests:
        if not _request_reaches(vuln, ev):
            continue
        out.reach = True
        out.reach_events += 1
        if ev.ts is not None and (out.first_reach_ts is None or ev.ts < out.first_reach_ts):
            out.first_reach_ts = ev.ts
        if out.exercise is False:
            hit, sample = _param_is_exercised(vuln, ev)
            if hit:
                out.exercise = True
                out.exercise_sample = sample

    for ev in stream.triggers:
        # A target emits its opaque signal; only platform-side emitters know ids.
        matched_by = None
        if vuln.oracle.signal and ev.signal == vuln.oracle.signal:
            matched_by = "signal"
        elif ev.vuln_id and ev.vuln_id == vuln.id:
            matched_by = "vuln_id"
        if matched_by is None:
            continue
        out.trigger = True
        out.trigger_events += 1
        out.trigger_source = out.trigger_source or matched_by
        if ev.ts is not None and (out.first_trigger_ts is None or ev.ts < out.first_trigger_ts):
            out.first_trigger_ts = ev.ts
        if ev.evidence and not out.evidence:
            out.evidence = dict(ev.evidence)

    for att in oob:
        out.trigger_events += 1
        out.attributions.append(att.as_dict())
        if att.confidence == "high":
            out.trigger = True
            out.trigger_source = out.trigger_source or f"oob:{att.kind}"
        else:
            # Counted, visible, but never part of the headline recall.
            out.trigger_low_confidence = True
        if att.ts is not None and (out.first_trigger_ts is None or att.ts < out.first_trigger_ts):
            out.first_trigger_ts = att.ts
        out.evidence.setdefault("oob_channel", att.channel)

    if out.trigger and not out.reach:
        # Exploited without an observed request: the SDK lost the request event.
        # Only proof promotes reach -- a container-window guess is not evidence
        # that the endpoint was ever touched.
        out.reach = True
        out.reach_inferred = True
    return out


# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #

def _metric(vulns: Sequence[Vuln], outcomes: Mapping[str, Outcome]) -> dict[str, Any]:
    """reach/exercise/trigger counts and recalls over a subset of the catalog."""
    block: dict[str, Any] = {"vulns": len(vulns)}
    for axis in AXES:
        applicable = 0
        hit = 0
        for v in vulns:
            value = getattr(outcomes[v.id], axis)
            if value is None:  # not applicable (exercise on a param-less vuln)
                continue
            applicable += 1
            hit += 1 if value else 0
        block[axis] = {
            "hit": hit,
            "applicable": applicable,
            # recall is null, not 0.0, when nothing is applicable: an empty cell is
            # "no ground truth here", which is a different statement from "failed".
            "recall": (hit / applicable) if applicable else None,
        }
    return block


def _group(
    vulns: Iterable[Vuln],
    outcomes: Mapping[str, Outcome],
    key: Callable[[Vuln], Any],
) -> dict[str, Any]:
    buckets: dict[str, list[Vuln]] = {}
    for v in vulns:
        k = key(v)
        if k is None:
            k = "unspecified"
        buckets.setdefault(str(k), []).append(v)
    return {k: _metric(buckets[k], outcomes) for k in sorted(buckets)}


def _group_multi(
    vulns: Iterable[Vuln],
    outcomes: Mapping[str, Outcome],
    key: Callable[[Vuln], Iterable[str]],
) -> dict[str, Any]:
    """Grouping where one vulnerability can belong to several buckets."""
    buckets: dict[str, list[Vuln]] = {}
    for v in vulns:
        keys = list(key(v)) or ["none"]
        for k in keys:
            buckets.setdefault(str(k), []).append(v)
    return {k: _metric(buckets[k], outcomes) for k in sorted(buckets)}


def _is_covered(entry: Any, observed: "_Observed") -> bool:
    """Was this inventory row walked?

    Rows are keyed by (app, host, method, route) but today's http_request events
    carry no host, so a request matches the row's host-collapsed key. When an SDK
    does report the vhost, the exact key is used and a request to one vhost stops
    crediting its hardened twins.
    """
    return entry.key in observed.with_host or entry.route_only_key in observed.routes


@dataclass
class _Observed:
    """Request keys actually seen, with and without a vhost."""

    # Host-less observations, which credit every row sharing that (method, route).
    routes: set[tuple[str, str, str]]
    # Observations that named a vhost; these credit only that vhost's row.
    with_host: set[tuple[str, str, str, str]]
    # Every observation, host or not, used to count traffic off the inventory.
    any_route: set[tuple[str, str, str]] = field(default_factory=set)


def _coverage_block(entries: Sequence[Any], observed: "_Observed") -> dict[str, Any]:
    covered = sum(1 for e in entries if _is_covered(e, observed))
    total = len(entries)
    return {
        "routes": total,
        "covered": covered,
        # null, not 0.0, when the inventory declares nothing here: an absent
        # denominator is not a failure to crawl.
        "coverage": (covered / total) if total else None,
    }


def _crawl_coverage(
    inventories: Mapping[str, RouteInventory],
    stream: EventStream,
    planted_reach: Mapping[str, Any],
    apps: Sequence[str] | None,
) -> dict[str, Any]:
    """Coverage of the whole published surface, next to the planted-only recall.

    Reach counts only the endpoints we made attractive, so on its own it flatters a
    tool that happens to walk the linked, interesting pages. The inventory lists the
    ordinary surface too (the contract requires at least three safe routes per
    planted one), which is the honest denominator for "did it crawl the site".
    Both are reported; the planted-only recall is copied in here verbatim so a
    reader never has to reconcile two tables to compare them.
    """
    selected = {a: inv for a, inv in inventories.items() if not apps or a in set(apps)}
    observed = _Observed(set(), set())
    hosts_reported = False
    for ev in stream.requests:
        route = normalize_route(ev.route)
        key = (ev.app or "", ev.method.upper(), route)
        observed.any_route.add(key)
        # The wire carries a hostname; the inventory names vhosts by their short
        # label. Resolve through the target's own inventory, and fall back to the
        # host-less behaviour when the name designates no declared vhost.
        inv = selected.get(ev.app or "")
        vhost = inv.resolve_host(ev.host) if (inv and ev.host) else None
        if vhost:
            hosts_reported = True
            observed.with_host.add((ev.app or "", vhost, ev.method.upper(), route))
        else:
            # No vhost on the wire: the visit cannot be attributed to one row, so it
            # credits them all. That inflation is reported by host_resolution.
            observed.routes.add(key)
    off_inventory = 0
    if selected:
        known = {e.route_only_key for inv in selected.values() for e in inv.routes}
        off_inventory = sum(1 for k in observed.any_route if k not in known)

    routes = [e for inv in selected.values() for e in inv.routes]
    planted = [e for e in routes if e.status == "planted"]
    safe = [e for e in routes if e.status != "planted"]

    def group(key: Callable[[Any], Any]) -> dict[str, Any]:
        buckets: dict[str, list[Any]] = {}
        for entry in routes:
            buckets.setdefault(str(key(entry) if key(entry) is not None else "unspecified"), []).append(entry)
        return {k: _coverage_block(buckets[k], observed) for k in sorted(buckets)}

    # Rows that share a (method, route) with a row on another vhost cannot be told
    # apart while requests carry no host: one visit credits all of them. Reported,
    # not hidden, because it inflates coverage for exactly the targets that serve
    # the same path from a hardened and an unhardened vhost.
    collapsed: dict[tuple[str, str, str], int] = {}
    for entry in routes:
        collapsed[entry.route_only_key] = collapsed.get(entry.route_only_key, 0) + 1
    ambiguous = sum(n for n in collapsed.values() if n > 1)

    return {
        "inventory_available": bool(selected),
        "apps": sorted(selected),
        "host_resolution": "host-aware" if hosts_reported else "collapsed",
        "hosts": sorted({h for inv in selected.values() for h in inv.hosts}),
        "rows_sharing_a_route_across_hosts": ambiguous,
        "surface": _coverage_block(routes, observed),
        "planted_routes": _coverage_block(planted, observed),
        "safe_routes": _coverage_block(safe, observed),
        "by_app": group(lambda e: e.app),
        "by_host": group(lambda e: e.host),
        "by_render": group(lambda e: e.render),
        "by_auth": group(lambda e: e.auth),
        # The biased denominator, kept side by side on purpose.
        "planted_vuln_reach": dict(planted_reach),
        "requests_off_inventory": off_inventory,
        "unvisited_routes": [
            {"app": e.app, "host": e.host, "method": e.method, "path": e.path,
             "route": e.route_key, "status": e.status, "render": e.render, "auth": e.auth}
            for e in sorted(routes, key=lambda x: (x.app, x.route_key, x.method))
            if not _is_covered(e, observed)
        ],
    }


def _chain_stats(
    catalog: Catalog, vulns: Sequence[Vuln], outcomes: Mapping[str, Outcome]
) -> dict[str, Any]:
    depths = {v.id: catalog.prereq_depth(v.id) for v in vulns}
    by_depth = _group(vulns, outcomes, lambda v: depths[v.id])

    completed = 0
    broken: list[dict[str, Any]] = []
    chained = [v for v in vulns if v.requires_prereq]
    for v in chained:
        prereqs = catalog.transitive_prereqs(v.id)
        missing = [p for p in prereqs if p in outcomes and not outcomes[p].trigger]
        if outcomes[v.id].trigger:
            if missing:
                # The tool exploited the tail of a chain without us observing the
                # head: either the chain is not really a chain, or a prerequisite
                # oracle failed to fire. Worth surfacing, never silently fixed.
                broken.append({"vuln_id": v.id, "missing_prereqs": missing})
            else:
                completed += 1
    return {
        "max_depth": max(depths.values(), default=0),
        "depth_histogram": {
            str(d): sum(1 for x in depths.values() if x == d)
            for d in sorted(set(depths.values()))
        },
        "by_depth": by_depth,
        "chained_vulns": len(chained),
        "chains_completed": completed,
        "chains_broken": broken,
    }


def score_run(
    catalog: Catalog,
    stream: EventStream,
    *,
    run: Mapping[str, Any] | None = None,
    findings: Mapping[str, Any] | None = None,
    apps: Sequence[str] | None = None,
    scope_source: str | None = None,
    inventories: Mapping[str, RouteInventory] | None = None,
) -> dict[str, Any]:
    """Build the full score document. See ``results/schema/score.schema.json``.

    ``apps`` restricts scoring to the targets that were actually in scope for the
    run; vulnerabilities in other apps are excluded from every denominator rather
    than counted as misses. ``inventories`` enables the whole-surface crawl
    coverage block; without it only the planted-only recall is reported.
    """
    scored_stream = stream.scored()
    catalog_apps = set(catalog.apps)

    # SCOPE. A run that scanned one app must not be scored against the whole corpus:
    # doing so understates a single-target run by the number of targets, which is a
    # far bigger error than anything the tool did. The run record's `targets` is the
    # authority; failing that the apps the events actually touched are a sound
    # derivation; only with neither is the headline corpus-wide, and then it says so.
    if apps:
        scope_apps = [a for a in apps if a in catalog_apps] or list(apps)
        scope = scope_source or "explicit"
    else:
        observed_apps = sorted({e.app for e in scored_stream.events if e.app} & catalog_apps)
        if observed_apps:
            scope_apps, scope = observed_apps, "events"
        else:
            scope_apps, scope = sorted(catalog_apps), "catalog"

    apps = scope_apps
    in_scope = [v for v in catalog.vulns if v.app in set(scope_apps)]
    record = normalize_run_record(run)
    addresses = address_index(record["containers"])
    oob_by_vuln, orphan_callbacks = attribute_oob_events(
        scored_stream, catalog, addresses=addresses
    )
    outcomes = {
        v.id: score_vuln(v, scored_stream, oob=oob_by_vuln.get(v.id, ()))
        for v in in_scope
    }

    warnings: list[dict[str, Any]] = []
    for v in in_scope:
        o = outcomes[v.id]
        if o.reach_inferred:
            warnings.append({
                "code": "trigger-without-reach",
                "vuln_id": v.id,
                "message": (
                    "trigger observed but no matching http_request event; reach was "
                    "credited anyway. The SDK for this target is under-reporting "
                    "requests, or the catalog route template does not match the one "
                    f"the framework registers ({v.entrypoint.method} {v.route_key})."
                ),
            })
        if o.trigger and o.exercise is False:
            warnings.append({
                "code": "trigger-without-exercise",
                "vuln_id": v.id,
                "message": (
                    f"trigger observed but parameter {v.entrypoint.param!r} was never "
                    f"seen carrying a non-default value in {v.entrypoint.param_in}; "
                    "exercise left false on purpose (see scoring.py docstring)."
                ),
            })

        if o.trigger_low_confidence and not o.trigger:
            warnings.append({
                "code": "low-confidence-trigger",
                "vuln_id": v.id,
                "message": (
                    "the only out-of-band attribution for this vulnerability was the "
                    "(container, time-window) fallback; counted in trigger_any and in "
                    "low_confidence_triggers, excluded from headline trigger recall"
                ),
            })

    unknown_trigger_ids = sorted({
        e.vuln_id for e in scored_stream.triggers
        if e.vuln_id and e.vuln_id not in catalog.by_id
    })
    for vid in unknown_trigger_ids:
        warnings.append({
            "code": "unknown-trigger-id",
            "vuln_id": vid,
            "message": "trigger event references an id absent from the catalog",
        })
    unknown_signals = sorted({
        e.signal for e in scored_stream.triggers
        if e.signal and e.signal not in catalog.by_signal
    })
    for signal in unknown_signals:
        warnings.append({
            "code": "unknown-signal",
            "vuln_id": None,
            "message": (
                f"a target emitted signal {signal!r}, which no catalog entry claims: "
                "either the catalog forgot a planted flaw, or a sink was renamed "
                "without updating oracle.signal. Nobody can be credited for it."
            ),
        })
    mode = record["scan_mode"] or {}
    if not mode:
        # Not inferred from the profile name: "baseline" means passive for one tool
        # and nothing for the next. Unknown is stated as unknown.
        warnings.append({
            "code": "scan-mode-unknown",
            "vuln_id": None,
            "message": (
                "the run record does not say whether the tool was permitted to attack, "
                "so a trigger recall of 0% here cannot be read as a capability result: "
                "it may mean the tool never tried."
            ),
        })
    if mode and (mode.get("active") is False or (mode.get("mode") or "").lower() in
                 {"passive", "baseline", "spider", "crawl"}):
        warnings.append({
            "code": "passive-scan-mode",
            "vuln_id": None,
            "message": (
                f"the run was {mode.get('mode') or 'passive'}"
                + (f" ({mode['reason']})" if mode.get("reason") else "")
                + ": the tool was never permitted to attack, so trigger recall is "
                "structurally zero and exercise recall is bounded by whatever the "
                "crawler happened to submit. These are not capability results and "
                "must not be compared with an active run."
            ),
        })

    if scope == "catalog" and len(catalog_apps) > 1:
        warnings.append({
            "code": "unscoped-run",
            "vuln_id": None,
            "message": (
                "neither the run record nor the events named a target, so the headline "
                f"is scored against all {len(catalog_apps)} apps in the corpus. A run "
                "that scanned one target is understated by that factor; pass --apps or "
                "score against a run record carrying `targets`."
            ),
        })
    elif scope == "events":
        warnings.append({
            "code": "scope-derived-from-events",
            "vuln_id": None,
            "message": (
                "the run record named no targets, so the scope was derived from the apps "
                f"the events touched: {', '.join(scope_apps)}. An app the tool never "
                "reached at all would be missing from this scope."
            ),
        })

    if not record["container_map_available"]:
        warnings.append({
            "code": "missing-container-map",
            "vuln_id": None,
            "message": (
                "the run export carries no container/address map, so a callback's "
                "source address cannot be resolved to a target. Nothing was inferred "
                "from address ranges (targets are dual-homed and their addresses have "
                "no numeric relationship); out-of-band callbacks that name no signal, "
                "token or app therefore stay unattributed."
            ),
        })
    for app, digests in sorted(record["reset_digests"].items()):
        if digests["match"] is False:
            warnings.append({
                "code": "reset-digest-mismatch",
                "vuln_id": None,
                "message": (
                    f"{app}: the seeded-state digest read before the run "
                    f"({digests['before']}) differs from the one read after "
                    f"({digests['after']}). The target did not return to its seeded "
                    "state, so whatever ran next was measured against a different "
                    "application and is not comparable with this run."
                ),
            })
    missing_record = [
        name for name, present in (
            ("image digests", bool(record["images"])),
            ("reset state digests", bool(record["reset_digests"])),
        ) if not present
    ]
    if missing_record:
        warnings.append({
            "code": "incomplete-run-record",
            "vuln_id": None,
            "message": (
                "the run record carries no " + " and no ".join(missing_record)
                + "; this score cannot be re-run from its own record"
            ),
        })

    for att in orphan_callbacks:
        warnings.append({
            "code": "unattributed-oob",
            "vuln_id": None,
            "message": (
                "out-of-band callback that could not be attributed to any catalog "
                f"entry (channel {att.channel}, destination {att.destination_host}, "
                f"container {att.container}); a blind flaw may have fired uncredited"
            ),
        })

    metrics = {
        "overall": _metric(in_scope, outcomes),
        "by_app": _group(in_scope, outcomes, lambda v: v.app),
        "by_owasp": {
            edition: _group(in_scope, outcomes, lambda v, e=edition: v.owasp_for(e))
            for edition in sorted(catalog.taxonomy.editions)
        },
        "by_family": _group(in_scope, outcomes, lambda v: v.family),
        "by_class": _group(in_scope, outcomes, lambda v: v.cls),
        "by_severity": _group(in_scope, outcomes, lambda v: v.severity),
        "by_render": _group(in_scope, outcomes, lambda v: v.discovery.render),
        "by_difficulty": _group(in_scope, outcomes, lambda v: v.discovery.difficulty),
        "by_auth": _group(in_scope, outcomes, lambda v: v.entrypoint.auth),
        "by_requires": _group_multi(in_scope, outcomes, lambda v: v.discovery.requires),
        "by_component": _group(in_scope, outcomes, lambda v: v.component),
        "by_oracle_kind": _group(in_scope, outcomes, lambda v: v.oracle.kind),
    }
    metrics["crawl"] = _crawl_coverage(
        inventories or {}, scored_stream, metrics["overall"]["reach"], apps
    )

    low_conf = [v.id for v in in_scope if outcomes[v.id].trigger_low_confidence]
    low_confidence_triggers = {
        "count": len(low_conf),
        "credited_only_here": sorted(
            v.id for v in in_scope
            if outcomes[v.id].trigger_low_confidence and not outcomes[v.id].trigger
        ),
        "vuln_ids": sorted(low_conf),
        # Both numbers, side by side, so nobody has to recompute one from the other.
        "headline_trigger": dict(metrics["overall"]["trigger"]),
        "inclusive_trigger": dict(metrics["overall"]["trigger_any"]),
        "attributions": [
            att
            for v in sorted(in_scope, key=lambda x: x.id)
            for att in outcomes[v.id].attributions
            if att.get("confidence") != "high"
        ],
        "unattributed_callbacks": [att.as_dict() for att in orphan_callbacks],
    }

    # The legend travels with the score so a report can be rendered from the JSON
    # alone, without re-reading the catalog. Reports are published artefacts; they
    # must not depend on a catalog revision that may have moved on.
    legend = {
        "owasp": {
            edition: dict(cats) for edition, cats in catalog.taxonomy.editions.items()
        },
        "families": list(catalog.taxonomy.families),
        "class_labels": {
            key: spec.get("label", key) for key, spec in catalog.taxonomy.classes.items()
        },
    }

    doc: dict[str, Any] = {
        "schema_version": SCORE_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "run": {
            "run_id": (run or {}).get("run_id"),
            "tool": (run or {}).get("tool", "unknown"),
            "tool_version": (run or {}).get("tool_version"),
            "profile": (run or {}).get("profile"),
            "targets": list((run or {}).get("targets") or apps or catalog.apps),
            "started_at": (run or {}).get("started_at"),
            "closed_at": (run or {}).get("closed_at"),
            "notes": (run or {}).get("notes"),
            # Provenance: a published number has to be re-runnable from its own
            # record, so the image actually running and the seeded-state digests
            # travel with the score rather than in someone's terminal history.
            "container_map_available": record["container_map_available"],
            "containers": record["containers"],
            "images": record["images"],
            "tool_image": record["tool_image"],
            "tool_image_digest": record["tool_image_digest"],
            "reset_digests": record["reset_digests"],
            "reset_consistent": record["reset_consistent"],
            # A reader who meets `trigger: 0.0%` has to learn on the same page
            # whether the tool failed or was never allowed to attack.
            "scan_mode": record["scan_mode"],
            "caveats": record["caveats"],
            "requests": record["requests"],
        },
        "scope": {
            "apps": list(scope_apps),
            "source": scope,
            "catalog_apps": sorted(catalog_apps),
            "vulns_in_scope": len(in_scope),
            "vulns_total": len(catalog.vulns),
        },
        "catalog": {
            "vulns_total": len(catalog.vulns),
            "vulns_in_scope": len(in_scope),
            "digest": catalog.digest(),
            "errors": len(catalog.errors),
            "warnings": len(catalog.warnings),
        },
        "events": stream.counts(),
        "legend": legend,
        "metrics": metrics,
        "low_confidence_triggers": low_confidence_triggers,
        "chains": _chain_stats(catalog, in_scope, outcomes),
        "vulns": [
            {
                "id": v.id,
                "title": v.title,
                "app": v.app,
                "component": v.component,
                "class": v.cls,
                "family": v.family,
                "severity": v.severity,
                "cwe": list(v.cwe),
                "owasp": dict(v.owasp),
                "method": v.entrypoint.method,
                "route": v.route_key,
                "param": v.entrypoint.param,
                "param_in": v.entrypoint.param_in,
                "auth": v.entrypoint.auth,
                "render": v.discovery.render,
                "difficulty": v.discovery.difficulty,
                "requires": list(v.discovery.requires),
                "oracle_kind": v.oracle.kind,
                "signal": v.oracle.signal,
                "requires_prereq": list(v.requires_prereq),
                **outcomes[v.id].as_dict(),
            }
            for v in sorted(in_scope, key=lambda x: x.id)
        ],
        "findings": findings,
        "warnings": warnings,
    }
    return doc
