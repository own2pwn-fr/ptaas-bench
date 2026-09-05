"""False-positive / precision analysis of a tool's own findings.

Recall (reach/exercise/trigger) is measured from inside the targets and cannot be
argued with. Precision cannot: it requires judging somebody else's report against
our ground truth, so every rule below is written down, applied mechanically, and
every ambiguous case is reported in its own bucket instead of being quietly folded
into TP or FP. A vendor who disagrees with a verdict must be able to point at the
exact rule.

INPUT -- a normalised findings file produced by a runner: a JSON array (or an
object with a ``findings`` key) of objects::

    {"tool": "zap", "url": "http://shopfront:8080/api/orders/1002?x=1",
     "method": "GET", "param": "id", "cwe": 639, "name": "...",
     "severity": "high", "confidence": "medium"}

``cwe`` accepts an int, a "CWE-639" string, or a list of either. ``app`` is honoured
when the runner supplies it; otherwise the app is inferred from the URL authority
through ``--app-map`` (``{"shopfront:8080": "shopfront"}``), and failing that the
finding is matched against every app in scope.

MATCHING RULES, in order:

1. *Location*. A candidate vulnerability matches when
   (a) its app equals the finding's app, when the app is known on both sides;
   (b) the finding's method equals the entrypoint method -- a finding with no
       method is accepted against any method;
   (c) the finding's URL path is an instance of the entrypoint route template
       (:func:`benchctl.routes.route_matches_path`, so ``/api/orders/1002``
       matches ``/api/orders/:id``);
   (d) the host agrees, when the host can discriminate. A finding always carries a
       host (it carries a URL), and where a target serves several vhosts the route
       inventory says which one hosts the planted flaw. ``/.git/config`` exposed on
       ``www`` and correctly refused on ``static`` is then two different verdicts
       rather than one collapsed row. When the target declares a single host, or
       the finding's host designates no declared vhost, matching falls back to
       host-agnostic and every row says so in ``host_match`` -- an assumption that
       changes a verdict has to be visible.
   No location match against any vulnerability => ``false-positive``. This is the
   only verdict that is unambiguously the tool's fault: it reported a hole where
   the ground truth says there is none.

2. *Parameter*. When the entrypoint declares a ``param`` and the finding declares a
   ``param``, they must be equal (case-insensitively). A finding with no ``param``
   is accepted -- many scanners report at endpoint granularity, and punishing that
   would measure reporting style rather than accuracy. A different parameter on the
   right endpoint lands in ``location-match, param-mismatch``.

3. *Class*. The finding's CWEs are compared with the vulnerability's resolved CWEs:
     * non-empty intersection            -> ``true-positive`` (``exact-cwe``)
     * no intersection, but both CWE sets belong to a common taxonomy *family*
       (families are derived from taxonomy.yaml itself, so CWE-89 and CWE-943 are
       both ``injection``)                -> ``true-positive`` (``cwe-family``)
     * no family agreement                -> ``location-match, class-mismatch``
     * finding carries no usable CWE      -> ``location-match, class-unknown``

4. *Duplicates*. The first finding that resolves to a vulnerability is the true
   positive; further findings from the same tool for the same vulnerability are
   ``duplicate``. Duplicates are neither TP nor FP -- they measure noise, reported
   separately as ``duplicate_ratio``.

5. *Ranking*. A finding is evaluated against every candidate and keeps its best
   verdict (true-positive > param-mismatch > class-mismatch > class-unknown), so a
   shared endpoint hosting two flaws is never penalised for the order of the files.

6. *Route inventory* (when the target publishes ``targets/<app>/routes.yaml``).
   A false positive is tagged with the reason it is one, because the two reasons
   are not equally defensible:
     * ``inventory-safe-route``   the inventory declares that exact route safe, and
       the contract requires safe routes to be written in the same style as planted
       ones. This is a confirmed false positive.
     * ``inventory-known-route``  the route is in the inventory but nothing planted
       matches the finding's shape (wrong method, say). Also confirmed: we have
       ground truth for that location.
     * ``unknown-route``          no inventory describes this route. Still counted
       as a false positive, but flagged as weaker evidence.
     * ``no-inventory``           the target publishes none at all.

PRECISION -- three numbers are published, never one:

    precision              = TP_unique / (TP_unique + FP)
    precision_conservative = TP_unique / (TP_unique + FP + ambiguous)
    precision_confirmed    = TP_unique / (TP_unique + FP_confirmed)

where *ambiguous* is the union of the three ``location-match, ...`` buckets and
*FP_confirmed* excludes false positives on routes no inventory describes. The
first is the friendly reading (a right-place-wrong-label finding still put a human
on the right endpoint), the second is the hostile one, the third is the one we can
defend route by route. Publishing all three means the choice of policy is the
reader's, not ours.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .catalog import Catalog, Vuln
from .inventory import STATUS_SAFE, RouteInventory, normalize_host
from .routes import path_from_url, route_matches_path

__all__ = [
    "Finding",
    "load_findings",
    "parse_cwes",
    "classify_findings",
    "VERDICT_TP",
    "VERDICT_FP",
    "VERDICT_DUP",
    "VERDICT_PARAM_MISMATCH",
    "VERDICT_CLASS_MISMATCH",
    "VERDICT_CLASS_UNKNOWN",
]

VERDICT_TP = "true-positive"
VERDICT_FP = "false-positive"
VERDICT_DUP = "duplicate"
VERDICT_PARAM_MISMATCH = "location-match, param-mismatch"
VERDICT_CLASS_MISMATCH = "location-match, class-mismatch"
VERDICT_CLASS_UNKNOWN = "location-match, class-unknown"

AMBIGUOUS_VERDICTS = (VERDICT_PARAM_MISMATCH, VERDICT_CLASS_MISMATCH, VERDICT_CLASS_UNKNOWN)

# Better verdicts first; used to pick the best candidate for one finding.
_RANK = {
    VERDICT_TP: 0,
    VERDICT_PARAM_MISMATCH: 1,
    VERDICT_CLASS_MISMATCH: 2,
    VERDICT_CLASS_UNKNOWN: 3,
    VERDICT_FP: 4,
}

_CWE_RE = re.compile(r"(\d+)")


@dataclass(frozen=True)
class Finding:
    tool: str | None = None
    url: str = ""
    method: str | None = None
    param: str | None = None
    cwe: tuple[int, ...] = ()
    name: str | None = None
    severity: str | None = None
    confidence: str | None = None
    app: str | None = None
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False)

    @property
    def path(self) -> str:
        return path_from_url(self.url)

    @property
    def host(self) -> str | None:
        """Hostname the finding was reported against, port stripped."""
        return normalize_host(self.authority)

    @property
    def authority(self) -> str | None:
        if "://" not in self.url:
            return None
        rest = self.url.split("://", 1)[1]
        return rest.split("/", 1)[0] or None


def parse_cwes(value: Any) -> tuple[int, ...]:
    """Accept 89, "89", "CWE-89", ["CWE-89", 943], None."""
    if value is None:
        return ()
    items: Iterable[Any] = value if isinstance(value, (list, tuple, set)) else [value]
    out: list[int] = []
    for item in items:
        if isinstance(item, bool):
            continue
        if isinstance(item, int):
            out.append(item)
            continue
        m = _CWE_RE.search(str(item))
        if m:
            out.append(int(m.group(1)))
    return tuple(dict.fromkeys(out))


def finding_from_dict(d: Mapping[str, Any]) -> Finding:
    return Finding(
        tool=d.get("tool"),
        url=str(d.get("url", "")),
        method=(str(d["method"]).upper() if d.get("method") else None),
        param=(d.get("param") or None),
        cwe=parse_cwes(d.get("cwe")),
        name=d.get("name"),
        severity=d.get("severity"),
        confidence=d.get("confidence"),
        app=d.get("app"),
        raw=d,
    )


def load_findings(path: Path | str) -> tuple[Finding, ...]:
    import json

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, Mapping):
        data = data.get("findings") or []
    return tuple(finding_from_dict(d) for d in data if isinstance(d, Mapping))


def _cwe_families(catalog: Catalog) -> dict[int, set[str]]:
    """CWE -> taxonomy families, derived from taxonomy.yaml (no hand-written map)."""
    out: dict[int, set[str]] = {}
    for spec in catalog.taxonomy.classes.values():
        fam = spec.get("family")
        if not fam:
            continue
        for cwe in spec.get("cwe") or ():
            out.setdefault(int(cwe), set()).add(fam)
    return out


def _resolve_app(f: Finding, app_map: Mapping[str, str] | None) -> str | None:
    if f.app:
        return f.app
    auth = f.authority
    if auth and app_map:
        if auth in app_map:
            return app_map[auth]
        host = auth.split(":", 1)[0]
        if host in app_map:
            return app_map[host]
    if auth:
        host = auth.split(":", 1)[0]
        return None if host in {"localhost", "127.0.0.1"} else host
    return None


def _judge(f: Finding, v: Vuln, cwe_fams: Mapping[int, set[str]]) -> tuple[str, str]:
    """Verdict of one finding against one location-matching vulnerability."""
    if v.entrypoint.param and f.param and f.param.lower() != v.entrypoint.param.lower():
        return VERDICT_PARAM_MISMATCH, (
            f"reported param {f.param!r}, ground truth injects through "
            f"{v.entrypoint.param!r}"
        )
    if not f.cwe:
        return VERDICT_CLASS_UNKNOWN, "finding carries no CWE, class cannot be checked"
    if set(f.cwe) & set(v.cwe):
        return VERDICT_TP, "exact-cwe"
    fam_f = set().union(*(cwe_fams.get(c, set()) for c in f.cwe)) if f.cwe else set()
    if v.family and v.family in fam_f:
        return VERDICT_TP, f"cwe-family ({v.family})"
    return VERDICT_CLASS_MISMATCH, (
        f"reported CWE {list(f.cwe)} ({sorted(fam_f) or 'unmapped'}) against a "
        f"{v.cls} ({v.family}, CWE {list(v.cwe)}) vulnerability"
    )


def _fp_basis(
    f: Finding,
    app: str | None,
    inventories: Mapping[str, RouteInventory] | None,
) -> tuple[str, str | None]:
    """Why this non-matching finding is a false positive, and how firmly."""
    if not inventories:
        return "no-inventory", None
    candidates = (
        [inventories[app]] if app and app in inventories
        else list(inventories.values()) if not app else []
    )
    for inv in candidates:
        entry = inv.match_path(f.method, f.path, host=f.host)
        if entry is None:
            continue
        if entry.status == STATUS_SAFE:
            return "inventory-safe-route", entry.status
        return "inventory-known-route", entry.status
    return "unknown-route", None


def _host_agrees(
    v: Vuln, inv: RouteInventory | None, resolved_host: str | None
) -> bool:
    """Is this vulnerability planted on the vhost the finding names?

    Only decides when it can: no inventory, no resolved host, or an entrypoint the
    inventory does not scope to a host all mean "no opinion", and the finding is
    matched host-agnostically.
    """
    if inv is None or resolved_host is None:
        return True
    planted_hosts = inv.planted_hosts(v.entrypoint.method, v.entrypoint.path)
    if not planted_hosts:
        return True
    return resolved_host in planted_hosts


def classify_findings(
    catalog: Catalog,
    findings: Sequence[Finding],
    *,
    outcomes: Mapping[str, Any] | None = None,
    app_map: Mapping[str, str] | None = None,
    apps: Sequence[str] | None = None,
    inventories: Mapping[str, RouteInventory] | None = None,
) -> dict[str, Any]:
    """Classify every finding and compute precision. See the module docstring.

    ``outcomes`` is the ``vulns`` list of a score document; when supplied, the
    report also cross-checks reporting against exploitation (which flaws a tool
    triggered but never reported, and which it reported without ever triggering).
    """
    cwe_fams = _cwe_families(catalog)
    in_scope = [v for v in catalog.vulns if not apps or v.app in set(apps)]

    claimed: set[tuple[str | None, str]] = set()
    rows: list[dict[str, Any]] = []

    for f in findings:
        app = _resolve_app(f, app_map)
        inv = (inventories or {}).get(app) if app else None
        resolved_host = inv.resolve_host(f.host) if inv else None
        if inv is None:
            host_match = "no-inventory"
        elif inv.single_host:
            # One vhost: the host carries no information, and saying so is honest.
            host_match = "agnostic-single-host"
        elif resolved_host:
            host_match = "exact"
        else:
            host_match = "agnostic-host-unresolved"

        candidates = [
            v for v in in_scope
            if (not app or not v.app or v.app == app)
            and (f.method is None or f.method == v.entrypoint.method)
            and route_matches_path(v.entrypoint.path, f.path)
            and _host_agrees(v, inv, resolved_host if host_match == "exact" else None)
        ]

        best: tuple[int, str, str, Vuln | None] = (_RANK[VERDICT_FP], VERDICT_FP,
                                                   "no catalog entrypoint matches this location", None)
        for v in candidates:
            verdict, reason = _judge(f, v, cwe_fams)
            rank = _RANK[verdict]
            if rank < best[0]:
                best = (rank, verdict, reason, v)

        _, verdict, reason, matched = best
        fp_basis: str | None = None
        inventory_status: str | None = None
        if verdict == VERDICT_FP:
            fp_basis, inventory_status = _fp_basis(f, app, inventories)
            if fp_basis == "inventory-safe-route":
                reason = "the route inventory declares this exact route safe"
            elif fp_basis == "inventory-known-route":
                reason = ("the route is in the inventory but nothing planted matches "
                          "this method/parameter shape")
        if verdict == VERDICT_TP and matched is not None:
            key = (f.tool, matched.id)
            if key in claimed:
                verdict, reason = VERDICT_DUP, f"already reported as {matched.id}"
            else:
                claimed.add(key)

        rows.append({
            "tool": f.tool,
            "url": f.url,
            "method": f.method,
            "param": f.param,
            "cwe": list(f.cwe),
            "name": f.name,
            "severity": f.severity,
            "confidence": f.confidence,
            "app": app,
            "host": f.host,
            "host_match": host_match,
            "verdict": verdict,
            "matched_vuln": matched.id if matched is not None else None,
            "reason": reason,
            "fp_basis": fp_basis,
            "inventory_status": inventory_status,
        })

    by_verdict: dict[str, int] = {}
    for row in rows:
        by_verdict[row["verdict"]] = by_verdict.get(row["verdict"], 0) + 1

    tp = by_verdict.get(VERDICT_TP, 0)
    fp = by_verdict.get(VERDICT_FP, 0)
    dup = by_verdict.get(VERDICT_DUP, 0)
    ambiguous = sum(by_verdict.get(v, 0) for v in AMBIGUOUS_VERDICTS)

    def ratio(num: int, den: int) -> float | None:
        return (num / den) if den else None

    reported = sorted({r["matched_vuln"] for r in rows
                       if r["matched_vuln"] and r["verdict"] in (VERDICT_TP, VERDICT_DUP)})
    cross: dict[str, Any] = {}
    if outcomes is not None:
        triggered = {o["id"] for o in outcomes if o.get("trigger")}
        cross = {
            "triggered_not_reported": sorted(triggered - set(reported)),
            "reported_not_triggered": sorted(set(reported) - triggered),
        }

    confirmed_fp = sum(
        1 for r in rows
        if r["verdict"] == VERDICT_FP
        and r["fp_basis"] in {"inventory-safe-route", "inventory-known-route"}
    )
    unknown_fp = fp - confirmed_fp

    return {
        "total": len(rows),
        "by_verdict": dict(sorted(by_verdict.items())),
        "inventory_available": bool(inventories),
        "true_positives": tp,
        "false_positives": fp,
        "false_positives_confirmed": confirmed_fp,
        "false_positives_unknown_route": unknown_fp,
        "duplicates": dup,
        "ambiguous": ambiguous,
        "ambiguous_breakdown": {v: by_verdict.get(v, 0) for v in AMBIGUOUS_VERDICTS},
        "precision": ratio(tp, tp + fp),
        "precision_conservative": ratio(tp, tp + fp + ambiguous),
        "precision_confirmed": ratio(tp, tp + confirmed_fp) if inventories else None,
        "duplicate_ratio": ratio(dup, len(rows)),
        "vulns_reported": reported,
        **cross,
        "false_positive_list": [r for r in rows if r["verdict"] == VERDICT_FP],
        "ambiguous_list": [r for r in rows if r["verdict"] in AMBIGUOUS_VERDICTS],
        "findings": rows,
    }
