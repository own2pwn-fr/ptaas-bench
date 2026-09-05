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
    A non-synthetic ``trigger`` event carrying the vulnerability id, or an ``oob``
    event whose token equals the vulnerability's ``oracle.canary_token``. OOB
    events are matched on the token alone: the token is unique per vulnerability
    (enforced by the catalog loader) and the canary service does not know which app
    made the callback.

Consistency: TRIGGER implies REACH. If a vulnerability triggered without a
matching request event, the report shows reach = true anyway and emits a
``trigger-without-reach`` warning, because the only possible explanation is an SDK
under-reporting requests -- and silently scoring reach = false there would
under-report the tool for a platform bug. The analogous
``trigger-without-exercise`` case is reported as a warning too, but does *not*
promote exercise: exercise is a claim about a specific parameter, and inferring it
from a trigger would let an out-of-band oracle manufacture a fuzzing claim we never
observed.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping, Sequence

from .catalog import Catalog, Vuln
from .events import EventStream, HttpRequestEvent
from .routes import routes_equal

__all__ = [
    "SCORE_SCHEMA_VERSION",
    "Outcome",
    "sha256_of",
    "param_in_matches",
    "score_vuln",
    "score_run",
]

SCORE_SCHEMA_VERSION = "1.0.0"

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


@dataclass
class Outcome:
    """Per-vulnerability verdict plus the evidence that produced it."""

    vuln_id: str
    reach: bool = False
    exercise: bool | None = None  # None = not applicable (no param declared)
    trigger: bool = False
    reach_events: int = 0
    trigger_events: int = 0
    reach_inferred: bool = False  # reach set from a trigger, no request seen
    first_reach_ts: float | None = None
    first_trigger_ts: float | None = None
    exercise_sample: str | None = None
    trigger_source: str | None = None  # "trigger-event" | "oob" | None
    evidence: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "vuln_id": self.vuln_id,
            "reach": self.reach,
            "exercise": self.exercise,
            "trigger": self.trigger,
            "reach_events": self.reach_events,
            "trigger_events": self.trigger_events,
            "reach_inferred": self.reach_inferred,
            "first_reach_ts": self.first_reach_ts,
            "first_trigger_ts": self.first_trigger_ts,
            "exercise_sample": self.exercise_sample,
            "trigger_source": self.trigger_source,
            "evidence": self.evidence,
        }


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


def score_vuln(vuln: Vuln, stream: EventStream) -> Outcome:
    """Score one vulnerability against an already synthetic-filtered stream."""
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
        if ev.vuln_id != vuln.id:
            continue
        out.trigger = True
        out.trigger_events += 1
        out.trigger_source = out.trigger_source or "trigger-event"
        if ev.ts is not None and (out.first_trigger_ts is None or ev.ts < out.first_trigger_ts):
            out.first_trigger_ts = ev.ts
        if ev.evidence and not out.evidence:
            out.evidence = dict(ev.evidence)

    token = vuln.oracle.canary_token
    if token:
        for ev in stream.oob:
            if ev.token != token:
                continue
            out.trigger = True
            out.trigger_events += 1
            out.trigger_source = out.trigger_source or "oob"
            if ev.ts is not None and (out.first_trigger_ts is None or ev.ts < out.first_trigger_ts):
                out.first_trigger_ts = ev.ts
            out.evidence.setdefault("oob_channel", ev.channel)

    if out.trigger and not out.reach:
        # Exploited without an observed request: the SDK lost the request event.
        out.reach = True
        out.reach_inferred = True
    return out


# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #

def _metric(vulns: Sequence[Vuln], outcomes: Mapping[str, Outcome]) -> dict[str, Any]:
    """reach/exercise/trigger counts and recalls over a subset of the catalog."""
    block: dict[str, Any] = {"vulns": len(vulns)}
    for axis in ("reach", "exercise", "trigger"):
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
) -> dict[str, Any]:
    """Build the full score document. See ``results/schema/score.schema.json``.

    ``apps`` restricts scoring to the targets that were actually in scope for the
    run; vulnerabilities in other apps are excluded from every denominator rather
    than counted as misses.
    """
    scored_stream = stream.scored()
    in_scope = [v for v in catalog.vulns if not apps or v.app in set(apps)]
    outcomes = {v.id: score_vuln(v, scored_stream) for v in in_scope}

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

    unknown_trigger_ids = sorted({
        e.vuln_id for e in scored_stream.triggers if e.vuln_id not in catalog.by_id
    })
    for vid in unknown_trigger_ids:
        warnings.append({
            "code": "unknown-trigger-id",
            "vuln_id": vid,
            "message": "trigger event references an id absent from the catalog",
        })
    unknown_tokens = sorted({
        e.token for e in scored_stream.oob if e.token not in catalog.by_token
    })
    for token in unknown_tokens:
        warnings.append({
            "code": "unknown-oob-token",
            "vuln_id": None,
            "message": f"OOB callback with token {token!r} matches no catalog vulnerability",
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
                "requires_prereq": list(v.requires_prereq),
                **outcomes[v.id].as_dict(),
            }
            for v in sorted(in_scope, key=lambda x: x.id)
        ],
        "findings": findings,
        "warnings": warnings,
    }
    return doc
