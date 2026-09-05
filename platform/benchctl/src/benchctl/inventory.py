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

VIRTUAL HOSTS. The route key carries a host, because one container can serve several
vhosts and the same path can be exposed on one and correctly refused on the others
(``infra`` serves ``www``, ``static`` and ``docs``; ``/.git/config`` is exposed on the
first and 403 on the other two). Without a host in the key those rows collapse, and
declaring the hardened ones ``safe`` would publish a tool that correctly reports the
exposed one as having raised a false positive -- the exact opposite of what the
target is built to measure.

``host: www`` or ``hosts: [www, static]`` on a row is optional; a row without one
inherits the inventory's canonical host (``host:``/``canonical_host:``, else the
authority of ``base_url:``), so every single-host inventory keeps working unchanged
and is matched host-agnostically. A row listing several hosts expands to one entry
per host. ``refused_equivalents:`` -- the section a target had to invent while the
key ignored the host -- is read as ``safe`` rows on its declared host, so those
hardened paths count for coverage and for precision again instead of being invisible.

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
    "host_matches",
    "normalize_host",
    "load_inventories",
    "crosscheck_inventory",
]

STATUS_SAFE = "safe"
STATUS_PLANTED = "planted"


def normalize_host(host: str | None) -> str | None:
    """Lower-case, strip the port and any trailing dot. None stays None."""
    if not host:
        return None
    value = str(host).strip().rstrip(".").lower()
    if value.startswith("[") and "]" in value:  # IPv6 literal with a port
        value = value[1:value.index("]")]
    elif value.count(":") == 1:
        value = value.split(":", 1)[0]
    return value or None


def host_matches(row_host: str | None, host: str | None) -> bool:
    """Does an observed host designate the vhost a row was written for?

    Inventories name vhosts by their short label (``www``) while a finding carries a
    fully-qualified name (``www.northlakefab.com``) or the harness alias, so the
    first DNS label is compared as well as the whole name. An unknown host matches
    nothing here; callers decide whether to fall back to host-agnostic matching.
    """
    a, b = normalize_host(row_host), normalize_host(host)
    if a is None or b is None:
        return True  # nothing declared on one side: not a discriminator
    if a == b:
        return True
    return a.split(".")[0] == b.split(".")[0]


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
    host: str | None = None
    # "routes" or "refused_equivalents": where the row was declared, kept so a
    # reader can tell a first-class route from a hardened equivalent.
    origin: str = "routes"
    expect_status: int | None = None

    @property
    def route_key(self) -> str:
        return normalize_route(self.path)

    @property
    def key(self) -> tuple[str, str, str, str]:
        return (self.app, normalize_host(self.host) or "", self.method.upper(),
                self.route_key)

    @property
    def route_only_key(self) -> tuple[str, str, str]:
        """Host-collapsed key, used where the observation carries no host."""
        return (self.app, self.method.upper(), self.route_key)


@dataclass
class RouteInventory:
    app: str
    routes: tuple[RouteEntry, ...]
    source: str | None = None
    canonical_host: str | None = None

    by_key: dict[tuple[str, str, str, str], RouteEntry] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        # Later duplicates lose; ambiguous rows are reported by load_inventories.
        self.by_key = {}
        for entry in self.routes:
            self.by_key.setdefault(entry.key, entry)

    @property
    def hosts(self) -> tuple[str, ...]:
        return tuple(sorted({normalize_host(r.host) for r in self.routes if r.host}))

    @property
    def single_host(self) -> bool:
        """True when the target serves one vhost, so a host cannot discriminate."""
        return len(self.hosts) <= 1

    def resolve_host(self, host: str | None) -> str | None:
        """The declared vhost an observed host designates, or None if unknown."""
        value = normalize_host(host)
        if value is None:
            return None
        for declared in self.hosts:
            if host_matches(declared, value):
                return declared
        return None

    def __len__(self) -> int:
        return len(self.routes)

    @property
    def planted(self) -> tuple[RouteEntry, ...]:
        return tuple(r for r in self.routes if r.status == STATUS_PLANTED)

    @property
    def safe(self) -> tuple[RouteEntry, ...]:
        return tuple(r for r in self.routes if r.status == STATUS_SAFE)

    def match_template(
        self, method: str, route: str, host: str | None = None
    ) -> RouteEntry | None:
        """Strict template lookup, the comparison used for crawl coverage."""
        for entry in self._candidates(method, host):
            if routes_equal(entry.path, route):
                return entry
        return None

    def match_path(
        self, method: str | None, path: str, host: str | None = None
    ) -> RouteEntry | None:
        """Lenient lookup of a concrete URL path, used to judge a finding.

        A literal route wins over a parameterised one: ``/api/orders/export`` must
        resolve to itself rather than to ``/api/orders/{id}``. When ``host`` names a
        vhost this target declares, only that vhost's rows are considered -- that is
        what makes a hardened ``/.git/config`` on one host a confirmed false
        positive while the exposed one on another host stays a true positive.
        """
        best: RouteEntry | None = None
        best_score = -1
        for entry in self._candidates(method, host):
            if not route_matches_path(entry.path, path):
                continue
            score = 1 if "{" not in entry.route_key else 0
            if score > best_score:
                best, best_score = entry, score
        return best

    def _candidates(self, method: str | None, host: str | None) -> list[RouteEntry]:
        resolved = self.resolve_host(host)
        return [
            entry for entry in self.routes
            if (method is None or entry.method.upper() == str(method).upper())
            # An unrecognised host is no filter at all: the caller is told the match
            # was host-agnostic rather than being handed a silent miss.
            and (resolved is None or host_matches(entry.host, resolved))
        ]

    def planted_hosts(self, method: str, route: str) -> tuple[str, ...]:
        """Which vhosts the inventory says host a planted flaw at this location."""
        return tuple(sorted({
            normalize_host(entry.host)
            for entry in self.routes
            if entry.status == STATUS_PLANTED
            and entry.method.upper() == str(method).upper()
            and routes_equal(entry.path, route)
            and entry.host
        }))


def _row_hosts(d: Mapping[str, Any], canonical: str | None) -> tuple[str | None, ...]:
    """Hosts a row applies to: `hosts: [...]`, `host: x`, else the canonical one."""
    hosts = d.get("hosts")
    if isinstance(hosts, (list, tuple)) and hosts:
        return tuple(normalize_host(h) for h in hosts)
    if d.get("host"):
        return (normalize_host(d["host"]),)
    return (normalize_host(canonical),)


def _entries_from_dict(
    app: str, d: Mapping[str, Any], canonical: str | None, origin: str = "routes"
) -> list[RouteEntry]:
    """One entry per host: a row listing three vhosts is three keys, not one."""
    base = {
        "app": app,
        "path": str(d.get("path", "/")),
        "method": str(d.get("method", "GET")).upper(),
        "auth": str(d.get("auth", "none")),
        "render": d.get("render"),
        "params": tuple(str(p) for p in (d.get("params") or ())),
        "status": str(d.get("status", STATUS_SAFE)),
        "notes": d.get("note") or d.get("notes"),
        "origin": origin,
        "expect_status": d.get("expect"),
    }
    return [RouteEntry(host=host, **base) for host in _row_hosts(d, canonical)]


def _canonical_host(data: Mapping[str, Any]) -> str | None:
    """The host a row inherits when it declares none."""
    for key in ("host", "canonical_host", "default_host"):
        if data.get(key):
            return normalize_host(data[key])
    base_url = data.get("base_url")
    if base_url:
        authority = str(base_url).split("://", 1)[-1].split("/", 1)[0]
        return normalize_host(authority)
    return None


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
        canonical = _canonical_host(data)
        entries: list[RouteEntry] = []
        for row in data.get("routes") or []:
            if isinstance(row, Mapping):
                entries.extend(_entries_from_dict(app, row, canonical))

        # `refused_equivalents` is the section a target had to invent while the route
        # key ignored the host: hardened paths that answer 403 on another vhost. Now
        # that the host is part of the key they are ordinary safe rows, and reading
        # them here puts them back into coverage and precision without waiting for
        # the file to be rewritten. Rows already declared under `routes` win.
        declared = {e.key for e in entries}
        for row in data.get("refused_equivalents") or []:
            if not isinstance(row, Mapping):
                continue
            for entry in _entries_from_dict(
                app, {**row, "status": STATUS_SAFE}, canonical, origin="refused_equivalents"
            ):
                if entry.key not in declared:
                    entries.append(entry)
                    declared.add(entry.key)
        for entry in entries:
            if entry.status not in {STATUS_SAFE, STATUS_PLANTED}:
                sink.append(
                    Issue("error", "inventory-bad-status",
                          f"{entry.method} {entry.path}: status {entry.status!r} is neither "
                          f"{STATUS_SAFE!r} nor {STATUS_PLANTED!r}", source=source)
                )
        # Two rows for one (method, route) with nothing to tell them apart would be
        # resolved by whichever row happened to be read last, which is exactly the
        # ambiguity the host field exists to remove.
        by_route: dict[tuple[str, str], list[RouteEntry]] = {}
        for entry in entries:
            by_route.setdefault((entry.method.upper(), entry.route_key), []).append(entry)
        for (method, route), rows in sorted(by_route.items()):
            if len(rows) < 2:
                continue
            hosts = [normalize_host(r.host) for r in rows]
            if len(set(hosts)) == len(hosts) and None not in hosts:
                continue  # distinct hosts: unambiguous
            sink.append(
                Issue("warning", "inventory-ambiguous-route",
                      f"{method} {route} is listed {len(rows)} times with no host to tell "
                      "the rows apart; whichever was read last would decide their status",
                      source=source)
            )

        if app in out:
            sink.append(
                Issue("error", "inventory-duplicate-app",
                      f"app {app!r} already loaded from {out[app].source}", source=source)
            )
            continue
        out[app] = RouteInventory(app=app, routes=tuple(entries), source=source,
                                  canonical_host=canonical)
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
