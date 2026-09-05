"""Route inventories: the whole reachable surface of each target.

Each target publishes ``targets/<app>/routes.yaml`` (format frozen in
``targets/target-contract.yaml``) listing every reachable route with
``status: safe | planted``. Two uses, both of which fix a measurement bias:

1. *Crawl coverage.* Reach measured over planted vulnerabilities alone is a biased
   denominator -- those are precisely the pages we made attractive, and several are
   linked from the homepage on purpose. Coverage over the published surface says
   what a crawler actually walked. Both numbers are reported; neither replaces the
   other, because they answer different questions ("did it find the interesting
   pages" vs "did it walk the site").

2. *A real false-positive denominator.* Without an inventory, a finding that
   matches no catalog entry is only *unmatched*: it might sit on a route we never
   described. When the inventory declares that exact route ``safe`` -- and the
   contract requires at least three safe endpoints per planted one, written in the
   same style -- the finding is a confirmed false positive.

Cross-checks between inventory and catalog are integrity errors rather than
warnings, because either direction silently corrupts scores: a planted entrypoint
missing from the inventory disappears from coverage, and a route the inventory
calls ``planted`` with no catalog entry is a flaw nobody can ever be credited for.
A route wrongly marked ``safe`` while hosting a planted flaw is worse still: real
detections there would be published as false positives.

An app with no ``routes.yaml`` at all is not an error here -- targets are written
after the catalog. It degrades to a warning, and only when some other target has
already published one (otherwise the feature is simply not deployed yet and there
is nothing useful to say).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import yaml

from .catalog import Catalog, Issue
from .routes import normalize_route, route_matches_path, routes_equal

__all__ = [
    "RouteEntry",
    "RouteInventory",
    "load_inventories",
    "crosscheck_inventory",
]

STATUS_SAFE = "safe"
STATUS_PLANTED = "planted"


@dataclass(frozen=True)
class RouteEntry:
    app: str
    path: str
    method: str = "GET"
    auth: str = "none"
    render: str | None = None
    params: tuple[str, ...] = ()
    status: str = STATUS_SAFE
    notes: str | None = None

    @property
    def route_key(self) -> str:
        return normalize_route(self.path)

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.app, self.method.upper(), self.route_key)


@dataclass
class RouteInventory:
    app: str
    routes: tuple[RouteEntry, ...]
    source: str | None = None

    by_key: dict[tuple[str, str, str], RouteEntry] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        # Later duplicates lose; duplicate keys are reported by crosscheck_inventory.
        self.by_key = {}
        for entry in self.routes:
            self.by_key.setdefault(entry.key, entry)

    def __len__(self) -> int:
        return len(self.routes)

    @property
    def planted(self) -> tuple[RouteEntry, ...]:
        return tuple(r for r in self.routes if r.status == STATUS_PLANTED)

    @property
    def safe(self) -> tuple[RouteEntry, ...]:
        return tuple(r for r in self.routes if r.status == STATUS_SAFE)

    def match_template(self, method: str, route: str) -> RouteEntry | None:
        """Strict template lookup, the comparison used for crawl coverage."""
        for entry in self.routes:
            if entry.method.upper() == method.upper() and routes_equal(entry.path, route):
                return entry
        return None

    def match_path(self, method: str | None, path: str) -> RouteEntry | None:
        """Lenient lookup of a concrete URL path, used to judge a finding.

        A literal route wins over a parameterised one: ``/api/orders/export`` must
        resolve to itself rather than to ``/api/orders/{id}``.
        """
        best: RouteEntry | None = None
        best_score = -1
        for entry in self.routes:
            if method is not None and entry.method.upper() != method.upper():
                continue
            if not route_matches_path(entry.path, path):
                continue
            score = 1 if "{" not in entry.route_key else 0
            if score > best_score:
                best, best_score = entry, score
        return best


def _entry_from_dict(app: str, d: Mapping[str, Any]) -> RouteEntry:
    return RouteEntry(
        app=app,
        path=str(d.get("path", "/")),
        method=str(d.get("method", "GET")).upper(),
        auth=str(d.get("auth", "none")),
        render=d.get("render"),
        params=tuple(str(p) for p in (d.get("params") or ())),
        status=str(d.get("status", STATUS_SAFE)),
        notes=d.get("notes"),
    )


def load_inventories(
    root: Path | str, *, issues: list[Issue] | None = None
) -> dict[str, RouteInventory]:
    """Load every ``targets/*/routes.yaml`` under ``root``."""
    sink = issues if issues is not None else []
    targets_dir = Path(root) / "targets"
    out: dict[str, RouteInventory] = {}
    if not targets_dir.is_dir():
        return out

    for path in sorted(targets_dir.glob("*/routes.yaml")):
        source = str(path)
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            sink.append(Issue("error", "inventory-parse", str(exc), source=source))
            continue
        if not isinstance(data, Mapping):
            sink.append(Issue("error", "inventory-shape", "file is not a mapping", source=source))
            continue

        dir_app = path.parent.name
        app = str(data.get("app") or dir_app)
        if data.get("app") and str(data["app"]) != dir_app:
            # The app key is the join between catalog, events and inventory; a
            # mismatch here silently splits one target into two in every table.
            sink.append(
                Issue("error", "inventory-app-mismatch",
                      f"declares app {data['app']!r} but lives in targets/{dir_app}/",
                      source=source)
            )
        entries = [
            _entry_from_dict(app, r)
            for r in (data.get("routes") or [])
            if isinstance(r, Mapping)
        ]
        for entry in entries:
            if entry.status not in {STATUS_SAFE, STATUS_PLANTED}:
                sink.append(
                    Issue("error", "inventory-bad-status",
                          f"{entry.method} {entry.path}: status {entry.status!r} is neither "
                          f"{STATUS_SAFE!r} nor {STATUS_PLANTED!r}", source=source)
                )
        seen: set[tuple[str, str, str]] = set()
        for entry in entries:
            if entry.key in seen:
                sink.append(
                    Issue("warning", "inventory-duplicate-route",
                          f"{entry.method} {entry.route_key} listed more than once", source=source)
                )
            seen.add(entry.key)

        if app in out:
            sink.append(
                Issue("error", "inventory-duplicate-app",
                      f"app {app!r} already loaded from {out[app].source}", source=source)
            )
            continue
        out[app] = RouteInventory(app=app, routes=tuple(entries), source=source)
    return out


def crosscheck_inventory(
    catalog: Catalog, inventories: Mapping[str, RouteInventory]
) -> list[Issue]:
    """Catalog and inventory must agree about which routes are planted."""
    issues: list[Issue] = []
    if not inventories:
        # Nothing published yet: the feature is not deployed, so there is nothing
        # to say. Silence beats a warning nobody can act on.
        return issues

    catalog_keys: dict[tuple[str, str, str], list[str]] = {}
    for v in catalog.vulns:
        catalog_keys.setdefault(
            (v.app, v.entrypoint.method.upper(), v.route_key), []
        ).append(v.id)

    for app in sorted({v.app for v in catalog.vulns}):
        inv = inventories.get(app)
        if inv is None:
            issues.append(
                Issue("warning", "inventory-missing",
                      f"app {app!r} has planted vulnerabilities but publishes no "
                      "targets/<app>/routes.yaml; crawl coverage and confirmed false "
                      "positives cannot be computed for it")
            )
            continue
        for v in catalog.for_app(app):
            entry = inv.match_template(v.entrypoint.method, v.entrypoint.path)
            if entry is None:
                issues.append(
                    Issue("error", "inventory-missing-entrypoint",
                          f"{v.entrypoint.method} {v.route_key} is not listed in "
                          f"{inv.source}; it would be invisible to crawl coverage",
                          vuln_id=v.id, source=v.source)
                )
            elif entry.status != STATUS_PLANTED:
                issues.append(
                    Issue("error", "inventory-status-mismatch",
                          f"{v.entrypoint.method} {v.route_key} is declared "
                          f"{entry.status!r} in {inv.source} while the catalog plants a "
                          "vulnerability there; real detections would be published as "
                          "false positives",
                          vuln_id=v.id, source=v.source)
                )

    for app, inv in sorted(inventories.items()):
        for entry in inv.planted:
            if not any(
                key[0] == app and key[1] == entry.method.upper()
                and routes_equal(key[2], entry.route_key)
                for key in catalog_keys
            ):
                issues.append(
                    Issue("error", "inventory-planted-uncatalogued",
                          f"{entry.method} {entry.route_key} is marked planted but no "
                          "catalog entry describes it; nothing could ever be credited "
                          "for finding it", source=inv.source)
                )
    return issues


def coverage_summary(
    inventories: Mapping[str, RouteInventory], apps: Sequence[str] | None = None
) -> dict[str, Any]:
    """Static description of the published surface, for ``bench catalog stats``."""
    selected = {
        app: inv for app, inv in inventories.items() if not apps or app in set(apps)
    }
    routes: list[RouteEntry] = [r for inv in selected.values() for r in inv.routes]

    def tally(values: Iterable[Any]) -> dict[str, int]:
        out: dict[str, int] = {}
        for value in values:
            out[str(value)] = out.get(str(value), 0) + 1
        return dict(sorted(out.items()))

    planted = sum(1 for r in routes if r.status == STATUS_PLANTED)
    safe = len(routes) - planted
    return {
        "apps": sorted(selected),
        "routes": len(routes),
        "planted": planted,
        "safe": safe,
        # The contract asks for at least three safe endpoints per planted one; a
        # target below that ratio is itself a tell and makes precision unmeasurable.
        "safe_per_planted": (safe / planted) if planted else None,
        "by_app": tally(r.app for r in routes),
        "by_render": tally(r.render for r in routes),
        "by_auth": tally(r.auth for r in routes),
    }
