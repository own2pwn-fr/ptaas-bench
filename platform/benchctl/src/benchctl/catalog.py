"""Catalog loading, validation and class-default resolution.

The catalog (``catalog/vulns/*.yaml`` + ``catalog/taxonomy.yaml``) is the ground
truth of the benchmark and this package is the only component allowed to read it.
Everything downstream consumes the typed objects built here, never raw YAML.

Resolution rule, applied per vulnerability: the taxonomy class supplies defaults
for ``cwe``, ``owasp`` (per edition), ``severity``, ``family`` and the human label;
an explicit field in the vulnerability file always wins. ``owasp`` is merged edition
by edition, so a file may override the 2025 mapping alone and keep the class default
for 2017 and 2021.

Integrity checks, and the level each one is reported at:

  error   schema violation (jsonschema, draft 2020-12) -- the file is unusable
  error   unknown ``class`` -- no defaults could be resolved
  error   duplicate ``id`` -- two files claim the same identifier, scores would
          silently merge
  error   id prefix disagrees with ``app`` (BENCH-SHOP-0001 must live in an app
          whose key starts with "shop"), or one prefix used for two apps
  error   ``requires_prereq`` referencing an unknown id, or a prerequisite cycle
  error   duplicate ``oracle.canary_token`` -- an OOB callback could not be
          attributed to a single vulnerability
  warning two vulnerabilities sharing one entrypoint (app + method + route). This
          is legitimate -- one endpoint can host both an IDOR and a SQLi -- but it
          means their reach scores are perfectly correlated, which a reader of the
          per-class table has to know.
  warning ``oracle.kind == oob`` without a ``canary_token``: the OOB channel can
          then never credit a trigger, only an in-app ``bench.trigger()`` can.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

import yaml
from jsonschema import Draft202012Validator

from .routes import normalize_route

__all__ = [
    "Issue",
    "Entrypoint",
    "Discovery",
    "Oracle",
    "Vuln",
    "Taxonomy",
    "Catalog",
    "load_catalog",
    "find_repo_root",
]

_ID_PREFIX_SEP = "-"


@dataclass(frozen=True)
class Issue:
    """One integrity problem found while loading the catalog."""

    level: str  # "error" | "warning"
    code: str
    message: str
    vuln_id: str | None = None
    source: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "code": self.code,
            "message": self.message,
            "vuln_id": self.vuln_id,
            "source": self.source,
        }

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        where = self.vuln_id or self.source or "catalog"
        return f"[{self.level}] {self.code}: {where}: {self.message}"


@dataclass(frozen=True)
class Entrypoint:
    method: str
    path: str
    auth: str = "none"
    param: str | None = None
    param_in: str = "query"
    default_value: str | None = None
    content_type: str | None = None

    @property
    def route_key(self) -> str:
        return normalize_route(self.path)


@dataclass(frozen=True)
class Discovery:
    render: str | None = None
    reachable_from: tuple[str, ...] = ()
    requires: tuple[str, ...] = ()
    difficulty: int | None = None


@dataclass(frozen=True)
class Oracle:
    kind: str
    condition: str
    poc: str | None = None
    canary_token: str | None = None


@dataclass(frozen=True)
class Vuln:
    id: str
    title: str
    app: str
    component: str
    cls: str
    family: str | None
    label: str | None
    cwe: tuple[int, ...]
    owasp: Mapping[str, str]
    severity: str
    entrypoint: Entrypoint
    discovery: Discovery
    oracle: Oracle
    requires_prereq: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    introduced: str | None = None
    notes: str | None = None
    source: str | None = None

    @property
    def has_param(self) -> bool:
        return bool(self.entrypoint.param)

    @property
    def route_key(self) -> str:
        return self.entrypoint.route_key

    def owasp_for(self, edition: str) -> str | None:
        return self.owasp.get(edition)


@dataclass(frozen=True)
class Taxonomy:
    editions: Mapping[str, Mapping[str, str]]
    families: tuple[str, ...]
    classes: Mapping[str, Mapping[str, Any]]

    def category_label(self, edition: str, code: str) -> str:
        return self.editions.get(edition, {}).get(code, code)


@dataclass
class Catalog:
    """Typed, validated view of the ground truth."""

    vulns: tuple[Vuln, ...]
    taxonomy: Taxonomy
    issues: tuple[Issue, ...] = ()
    root: Path | None = None

    by_id: dict[str, Vuln] = field(init=False, repr=False)
    by_token: dict[str, Vuln] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.by_id = {v.id: v for v in self.vulns}
        self.by_token = {
            v.oracle.canary_token: v for v in self.vulns if v.oracle.canary_token
        }

    def __iter__(self) -> Iterator[Vuln]:
        return iter(self.vulns)

    def __len__(self) -> int:
        return len(self.vulns)

    @property
    def errors(self) -> tuple[Issue, ...]:
        return tuple(i for i in self.issues if i.level == "error")

    @property
    def warnings(self) -> tuple[Issue, ...]:
        return tuple(i for i in self.issues if i.level == "warning")

    @property
    def apps(self) -> tuple[str, ...]:
        return tuple(sorted({v.app for v in self.vulns}))

    def for_app(self, app: str) -> tuple[Vuln, ...]:
        return tuple(v for v in self.vulns if v.app == app)

    def digest(self) -> str:
        """Stable fingerprint of the ground truth a score was computed against."""
        h = hashlib.sha256()
        for v in sorted(self.vulns, key=lambda x: x.id):
            h.update(
                json.dumps(
                    [v.id, v.app, v.cls, v.severity, v.entrypoint.method, v.route_key,
                     v.entrypoint.param, v.entrypoint.param_in, v.entrypoint.default_value],
                    sort_keys=True,
                ).encode()
            )
        return h.hexdigest()[:16]

    def prereq_depth(self, vuln_id: str, _seen: frozenset[str] = frozenset()) -> int:
        """Longest prerequisite chain ending at ``vuln_id`` (0 = standalone)."""
        v = self.by_id.get(vuln_id)
        if v is None or not v.requires_prereq or vuln_id in _seen:
            return 0
        seen = _seen | {vuln_id}
        depths = [
            1 + self.prereq_depth(p, seen) for p in v.requires_prereq if p in self.by_id
        ]
        return max(depths, default=0)

    def transitive_prereqs(self, vuln_id: str) -> tuple[str, ...]:
        out: list[str] = []
        stack = list(self.by_id[vuln_id].requires_prereq) if vuln_id in self.by_id else []
        seen: set[str] = set()
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            out.append(cur)
            nxt = self.by_id.get(cur)
            if nxt is not None:
                stack.extend(nxt.requires_prereq)
        return tuple(sorted(out))


def find_repo_root(start: Path | None = None) -> Path:
    """Walk up from ``start`` until a directory containing ``catalog/`` is found."""
    cur = (start or Path.cwd()).resolve()
    for candidate in [cur, *cur.parents]:
        if (candidate / "catalog" / "taxonomy.yaml").is_file():
            return candidate
    raise FileNotFoundError(
        "could not locate the ptaas-bench repository root (no catalog/taxonomy.yaml "
        f"found at or above {cur})"
    )


def _load_yaml(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _load_taxonomy(path: Path, issues: list[Issue]) -> Taxonomy:
    data = _load_yaml(path) or {}
    editions = {str(k): dict(v or {}) for k, v in (data.get("editions") or {}).items()}
    families = tuple(data.get("families") or ())
    classes = {str(k): dict(v or {}) for k, v in (data.get("classes") or {}).items()}

    for key, spec in classes.items():
        fam = spec.get("family")
        if fam and families and fam not in families:
            issues.append(
                Issue("error", "taxonomy-unknown-family",
                      f"class {key!r} declares family {fam!r} which is not in `families`",
                      source=str(path))
            )
        for edition, code in (spec.get("owasp") or {}).items():
            if str(edition) in editions and code not in editions[str(edition)]:
                issues.append(
                    Issue("error", "taxonomy-unknown-category",
                          f"class {key!r} maps to {edition}:{code} which is not a "
                          f"category of that edition", source=str(path))
                )
    return Taxonomy(editions=editions, families=families, classes=classes)


def _alnum(s: str) -> str:
    return "".join(ch for ch in s.casefold() if ch.isalnum())


def _resolve(raw: Mapping[str, Any], spec: Mapping[str, Any]) -> dict[str, Any]:
    """Merge class defaults with per-vulnerability overrides (override wins)."""
    cwe = raw.get("cwe") or spec.get("cwe") or []
    severity = raw.get("severity") or spec.get("severity")
    owasp = dict(spec.get("owasp") or {})
    owasp = {str(k): v for k, v in owasp.items()}
    for edition, code in (raw.get("owasp") or {}).items():
        owasp[str(edition)] = code  # per-edition override
    return {
        "cwe": tuple(int(c) for c in cwe),
        "severity": severity,
        "owasp": owasp,
        "family": spec.get("family"),
        "label": spec.get("label"),
    }


def _build_vuln(raw: Mapping[str, Any], spec: Mapping[str, Any], source: str) -> Vuln:
    res = _resolve(raw, spec)
    ep_raw = raw.get("entrypoint") or {}
    disc_raw = raw.get("discovery") or {}
    or_raw = raw.get("oracle") or {}
    return Vuln(
        id=raw["id"],
        title=raw.get("title", ""),
        app=raw.get("app", ""),
        component=raw.get("component", "api"),
        cls=raw.get("class", ""),
        family=res["family"],
        label=res["label"],
        cwe=res["cwe"],
        owasp=res["owasp"],
        severity=res["severity"] or "info",
        entrypoint=Entrypoint(
            method=str(ep_raw.get("method", "GET")).upper(),
            path=ep_raw.get("path", "/"),
            auth=ep_raw.get("auth", "none"),
            param=ep_raw.get("param"),
            param_in=ep_raw.get("param_in", "query"),
            default_value=ep_raw.get("default_value"),
            content_type=ep_raw.get("content_type"),
        ),
        discovery=Discovery(
            render=disc_raw.get("render"),
            reachable_from=tuple(disc_raw.get("reachable_from") or ()),
            requires=tuple(disc_raw.get("requires") or ()),
            difficulty=disc_raw.get("difficulty"),
        ),
        oracle=Oracle(
            kind=or_raw.get("kind", "sink"),
            condition=or_raw.get("condition", ""),
            poc=or_raw.get("poc"),
            canary_token=or_raw.get("canary_token"),
        ),
        requires_prereq=tuple(raw.get("requires_prereq") or ()),
        tags=tuple(raw.get("tags") or ()),
        introduced=str(raw["introduced"]) if raw.get("introduced") else None,
        notes=raw.get("notes"),
        source=source,
    )


def load_catalog(
    root: Path | str | None = None,
    *,
    vulns_dir: Path | str | None = None,
    taxonomy_path: Path | str | None = None,
    schema_path: Path | str | None = None,
) -> Catalog:
    """Load, validate and resolve the whole catalog.

    Never raises on catalog content: problems are collected as :class:`Issue`
    objects on the returned :class:`Catalog` so ``bench validate`` can print all of
    them at once instead of stopping at the first bad file.
    """
    root_path = Path(root) if root is not None else find_repo_root()
    vdir = Path(vulns_dir) if vulns_dir else root_path / "catalog" / "vulns"
    tpath = Path(taxonomy_path) if taxonomy_path else root_path / "catalog" / "taxonomy.yaml"
    spath = Path(schema_path) if schema_path else root_path / "catalog" / "schema.json"

    issues: list[Issue] = []
    taxonomy = _load_taxonomy(tpath, issues)
    validator = Draft202012Validator(json.loads(Path(spath).read_text(encoding="utf-8")))

    vulns: list[Vuln] = []
    seen_ids: dict[str, str] = {}
    prefix_to_app: dict[str, str] = {}
    entrypoints: dict[tuple[str, str, str], list[str]] = {}
    tokens: dict[str, str] = {}

    for path in sorted(vdir.glob("*.yaml")) if vdir.is_dir() else []:
        source = str(path)
        try:
            raw = _load_yaml(path)
        except yaml.YAMLError as exc:
            issues.append(Issue("error", "yaml-parse", str(exc), source=source))
            continue
        if not isinstance(raw, dict):
            issues.append(Issue("error", "yaml-shape", "file is not a mapping", source=source))
            continue

        errs = sorted(validator.iter_errors(raw), key=lambda e: list(e.path))
        for err in errs:
            loc = "/".join(str(p) for p in err.path) or "<root>"
            issues.append(
                Issue("error", "schema", f"{loc}: {err.message}",
                      vuln_id=raw.get("id"), source=source)
            )
        if errs:
            continue

        vid = raw["id"]
        if vid in seen_ids:
            issues.append(
                Issue("error", "duplicate-id",
                      f"id already defined in {seen_ids[vid]}", vuln_id=vid, source=source)
            )
            continue
        seen_ids[vid] = source

        cls = raw.get("class", "")
        spec = taxonomy.classes.get(cls)
        if spec is None:
            issues.append(
                Issue("error", "unknown-class",
                      f"class {cls!r} is not defined in taxonomy.yaml",
                      vuln_id=vid, source=source)
            )
            spec = {}

        # id prefix vs app: BENCH-SHOP-0001 must belong to an app key starting with
        # "shop". Keeps ids greppable and stops a copy-pasted file from scoring
        # against the wrong target.
        prefix = vid.split(_ID_PREFIX_SEP)[1] if vid.count(_ID_PREFIX_SEP) >= 2 else ""
        app = raw.get("app", "")
        if prefix and app and not _alnum(app).startswith(_alnum(prefix)):
            issues.append(
                Issue("error", "id-app-mismatch",
                      f"id prefix {prefix!r} does not match app {app!r}",
                      vuln_id=vid, source=source)
            )
        elif prefix:
            known = prefix_to_app.setdefault(prefix, app)
            if known != app:
                issues.append(
                    Issue("error", "id-prefix-collision",
                          f"prefix {prefix!r} is already used by app {known!r}",
                          vuln_id=vid, source=source)
                )

        vuln = _build_vuln(raw, spec, source)
        vulns.append(vuln)

        key = (vuln.app, vuln.entrypoint.method, vuln.route_key)
        entrypoints.setdefault(key, []).append(vid)

        token = vuln.oracle.canary_token
        if token:
            if token in tokens:
                issues.append(
                    Issue("error", "duplicate-canary-token",
                          f"canary token {token!r} is already used by {tokens[token]}; "
                          "an OOB callback could not be attributed",
                          vuln_id=vid, source=source)
                )
            else:
                tokens[token] = vid
        elif vuln.oracle.kind == "oob":
            issues.append(
                Issue("warning", "oob-without-token",
                      "oracle.kind is 'oob' but no canary_token is declared; only an "
                      "in-app trigger event can ever credit this vulnerability",
                      vuln_id=vid, source=source)
            )

    known_ids = {v.id for v in vulns}
    for v in vulns:
        for prereq in v.requires_prereq:
            if prereq not in known_ids:
                issues.append(
                    Issue("error", "unknown-prereq",
                          f"requires_prereq references unknown id {prereq!r}",
                          vuln_id=v.id, source=v.source)
                )

    for cycle_id in _find_cycles({v.id: v.requires_prereq for v in vulns}):
        issues.append(
            Issue("error", "prereq-cycle",
                  "requires_prereq forms a cycle, chain depth is undefined",
                  vuln_id=cycle_id)
        )

    for (app, method, route), ids in sorted(entrypoints.items()):
        if len(ids) > 1:
            issues.append(
                Issue("warning", "shared-entrypoint",
                      f"{method} {route} on {app} is shared by {', '.join(sorted(ids))}; "
                      "their reach scores are perfectly correlated",
                      vuln_id=sorted(ids)[0])
            )

    return Catalog(
        vulns=tuple(vulns), taxonomy=taxonomy, issues=tuple(issues), root=root_path
    )


def _find_cycles(graph: Mapping[str, Sequence[str]]) -> list[str]:
    """Return the ids that participate in at least one prerequisite cycle."""
    WHITE, GREY, BLACK = 0, 1, 2
    colour = dict.fromkeys(graph, WHITE)
    bad: set[str] = set()

    def visit(node: str, stack: list[str]) -> None:
        colour[node] = GREY
        stack.append(node)
        for nxt in graph.get(node, ()):
            if nxt not in colour:
                continue
            if colour[nxt] == GREY:
                bad.update(stack[stack.index(nxt):])
            elif colour[nxt] == WHITE:
                visit(nxt, stack)
        stack.pop()
        colour[node] = BLACK

    for node in list(graph):
        if colour[node] == WHITE:
            visit(node, [])
    return sorted(bad)


def coverage_stats(catalog: Catalog) -> dict[str, Any]:
    """Coverage matrix used by ``bench catalog stats`` to drive the catalog to 150+.

    Reports, per OWASP edition, how many planted vulnerabilities land in each
    category (zero-cells are the backlog), plus the classes and families that have
    no instance at all, and the distribution across apps / render modes / auth
    levels / difficulty.
    """
    counts_by_edition: dict[str, dict[str, int]] = {}
    for edition, categories in catalog.taxonomy.editions.items():
        cell = {code: 0 for code in categories}
        for v in catalog.vulns:
            code = v.owasp_for(edition)
            if code in cell:
                cell[code] += 1
        counts_by_edition[edition] = cell

    per_class = {key: 0 for key in catalog.taxonomy.classes}
    for v in catalog.vulns:
        if v.cls in per_class:
            per_class[v.cls] += 1

    per_family = {fam: 0 for fam in catalog.taxonomy.families}
    for v in catalog.vulns:
        if v.family in per_family:
            per_family[v.family] += 1

    def tally(values: Iterable[Any]) -> dict[str, int]:
        out: dict[str, int] = {}
        for value in values:
            out[str(value)] = out.get(str(value), 0) + 1
        return dict(sorted(out.items()))

    return {
        "total_vulns": len(catalog.vulns),
        "total_classes": len(catalog.taxonomy.classes),
        "classes_covered": sum(1 for n in per_class.values() if n),
        "owasp": {
            edition: {
                "counts": cells,
                "empty_cells": sorted(c for c, n in cells.items() if n == 0),
                "labels": dict(catalog.taxonomy.editions[edition]),
            }
            for edition, cells in counts_by_edition.items()
        },
        "classes": per_class,
        "empty_classes": sorted(c for c, n in per_class.items() if n == 0),
        "families": per_family,
        "empty_families": sorted(f for f, n in per_family.items() if n == 0),
        "by_app": tally(v.app for v in catalog.vulns),
        "by_severity": tally(v.severity for v in catalog.vulns),
        "by_render": tally(v.discovery.render for v in catalog.vulns),
        "by_auth": tally(v.entrypoint.auth for v in catalog.vulns),
        "by_difficulty": tally(v.discovery.difficulty for v in catalog.vulns),
        "by_component": tally(v.component for v in catalog.vulns),
        "params": {
            "with_param": sum(1 for v in catalog.vulns if v.has_param),
            "without_param": sum(1 for v in catalog.vulns if not v.has_param),
        },
    }
