"""``bench`` command line.

    bench validate                     catalog integrity (exit 1 on any error)
    bench score --run <id>             turn a run's events into a score document
    bench report --runs a,b,c          render N score documents into results/
    bench catalog stats                coverage matrix, i.e. the catalog backlog

Every command takes ``--root`` to point at a checkout other than the one the
current directory belongs to; by default the repository root is discovered by
walking up until ``catalog/taxonomy.yaml`` appears, so the CLI works from
anywhere inside the tree.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from . import __version__
from .catalog import Catalog, Issue, coverage_stats, find_repo_root, load_catalog
from .events import (
    EventStream,
    address_index,
    fetch_events,
    load_events,
    normalize_run_record,
)
from .findings import classify_findings, load_findings
from .inventory import coverage_summary, crosscheck_inventory, load_inventories
from .report import load_score, write_report
from .scoring import score_run

__all__ = ["main", "build_parser"]

_DEFAULT_COLLECTOR = "http://collector:8900"


def _root(args: argparse.Namespace) -> Path:
    return Path(args.root).resolve() if args.root else find_repo_root()


def _load(args: argparse.Namespace) -> Catalog:
    """Catalog plus the route inventories, cross-checked against each other.

    The cross-check lives here rather than in load_catalog because it spans two
    components; its issues are appended to the catalog's so `bench validate` shows
    one list and exits non-zero on either kind of error.
    """
    root = _root(args)
    catalog = load_catalog(root)
    inventory_issues: list[Issue] = []
    inventories = load_inventories(root, issues=inventory_issues)
    inventory_issues.extend(crosscheck_inventory(catalog, inventories))
    if inventory_issues:
        catalog.issues = tuple(catalog.issues) + tuple(inventory_issues)
    return catalog


def _load_with_inventories(
    args: argparse.Namespace,
) -> tuple[Catalog, dict[str, Any]]:
    root = _root(args)
    return _load(args), load_inventories(root)


def _emit(obj: Any) -> None:
    json.dump(obj, sys.stdout, indent=2, sort_keys=False)
    sys.stdout.write("\n")


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


# --------------------------------------------------------------------------- #
# bench validate
# --------------------------------------------------------------------------- #

def cmd_validate(args: argparse.Namespace) -> int:
    catalog = _load(args)
    if args.json:
        _emit({
            "vulns": len(catalog),
            "apps": list(catalog.apps),
            "digest": catalog.digest(),
            "errors": [i.as_dict() for i in catalog.errors],
            "warnings": [i.as_dict() for i in catalog.warnings],
        })
    else:
        for issue in catalog.issues:
            print(issue, file=sys.stderr if issue.level == "error" else sys.stdout)
        print(
            f"{len(catalog)} vulnerabilities across {len(catalog.apps)} app(s), "
            f"digest {catalog.digest()}: "
            f"{len(catalog.errors)} error(s), {len(catalog.warnings)} warning(s)"
        )
    return 1 if catalog.errors else 0


# --------------------------------------------------------------------------- #
# bench score
# --------------------------------------------------------------------------- #

def cmd_score(args: argparse.Namespace) -> int:
    catalog, inventories = _load_with_inventories(args)
    if catalog.errors and not args.ignore_catalog_errors:
        print(
            f"refusing to score against a catalog with {len(catalog.errors)} error(s); "
            "run `bench validate` (or pass --ignore-catalog-errors)",
            file=sys.stderr,
        )
        return 2

    if args.events:
        stream, meta = load_events(args.events)
    else:
        stream, meta = fetch_events(args.collector, args.run)
    meta = dict(meta)
    meta.setdefault("run_id", args.run)
    if args.tool:
        meta["tool"] = args.tool
    if args.tool_version:
        meta["tool_version"] = args.tool_version
    if args.profile:
        meta["profile"] = args.profile

    apps: Sequence[str] | None = None
    if args.apps:
        apps = [a.strip() for a in args.apps.split(",") if a.strip()]
    elif meta.get("targets"):
        apps = list(meta["targets"])

    findings_block = None
    if args.findings:
        # The run's container map already says which address and service name belong
        # to which app, so findings that cite a container IP resolve without anyone
        # writing a mapping by hand. An explicit --app-map still wins.
        record = normalize_run_record(meta)
        app_map = dict(address_index(record["containers"]))
        if args.app_map:
            app_map.update(json.loads(Path(args.app_map).read_text(encoding="utf-8")))
        preliminary = score_run(catalog, stream, run=meta, apps=apps,
                                inventories=inventories)
        findings_block = classify_findings(
            catalog,
            load_findings(args.findings),
            outcomes=preliminary["vulns"],
            app_map=app_map,
            apps=apps,
            inventories=inventories,
        )

    doc = score_run(catalog, stream, run=meta, findings=findings_block, apps=apps,
                    inventories=inventories)

    out = Path(args.out) if args.out else Path(_root(args)) / "results" / "runs" / str(args.run) / "score.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")

    if args.json:
        _emit(doc)
    else:
        overall = doc["metrics"]["overall"]
        print(f"run {doc['run']['run_id']} · tool {doc['run']['tool']} · {out}")
        print(f"  events   : {doc['events']}")
        for axis in ("reach", "exercise", "trigger", "trigger_any"):
            block = overall[axis]
            label = "trig+weak" if axis == "trigger_any" else axis
            print(f"  {label:<9}: {_pct(block['recall'])} ({block['hit']}/{block['applicable']})")
        low = doc["low_confidence_triggers"]
        if low["credited_only_here"]:
            print(f"  weak-oob : {', '.join(low['credited_only_here'])} "
                  "(container/time-window attribution only, excluded from headline)")
        crawl = doc["metrics"]["crawl"]
        if crawl["inventory_available"]:
            surface = crawl["surface"]
            print(f"  crawl    : {_pct(surface['coverage'])} of the published surface "
                  f"({surface['covered']}/{surface['routes']} routes)")
        if findings_block:
            print(
                f"  precision: {_pct(findings_block['precision'])} "
                f"(TP {findings_block['true_positives']}, FP {findings_block['false_positives']}"
                f" of which {findings_block['false_positives_confirmed']} confirmed by the "
                f"inventory, ambiguous {findings_block['ambiguous']})"
            )
        if doc["run"]["reset_consistent"] is False:
            print("  ! state reset DIRTY: the target did not return to its seeded "
                  "state, later runs are not comparable", file=sys.stderr)
        for warning in doc["warnings"]:
            print(f"  ! {warning['code']} {warning['vuln_id'] or ''}", file=sys.stderr)
    return 0


# --------------------------------------------------------------------------- #
# bench report
# --------------------------------------------------------------------------- #

def _resolve_run(token: str, root: Path, results_dir: Path) -> Path:
    candidates = [
        Path(token),
        results_dir / token,
        results_dir / "runs" / token / "score.json",
        root / "results" / "runs" / token / "score.json",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise SystemExit(
        f"cannot resolve run {token!r}: tried " + ", ".join(str(c) for c in candidates)
    )


def cmd_report(args: argparse.Namespace) -> int:
    root = _root(args)
    results_dir = Path(args.results_dir) if args.results_dir else root / "results"
    tokens = [t.strip() for t in args.runs.split(",") if t.strip()]
    docs = [load_score(_resolve_run(t, root, results_dir)) for t in tokens]
    out_dir = Path(args.out) if args.out else results_dir
    written = write_report(docs, out_dir)
    for path in written:
        print(path)
    return 0


# --------------------------------------------------------------------------- #
# bench catalog stats
# --------------------------------------------------------------------------- #

def cmd_catalog_stats(args: argparse.Namespace) -> int:
    catalog, inventories = _load_with_inventories(args)
    stats = coverage_stats(catalog)
    stats["surface"] = coverage_summary(inventories)
    if args.json:
        _emit(stats)
        return 0

    print(f"{stats['total_vulns']} planted vulnerabilities · "
          f"{stats['classes_covered']}/{stats['total_classes']} taxonomy classes covered")
    for edition in sorted(stats["owasp"]):
        cells = stats["owasp"][edition]
        labels = cells["labels"]
        print(f"\nOWASP {edition}")
        for code in sorted(cells["counts"]):
            count = cells["counts"][code]
            flag = "  <- EMPTY" if count == 0 else ""
            print(f"  {code} {labels.get(code, ''):<42} {count:>4}{flag}")
    print("\nBy family")
    for fam, count in stats["families"].items():
        print(f"  {fam:<20} {count:>4}" + ("  <- EMPTY" if count == 0 else ""))
    for axis in ("by_app", "by_render", "by_auth", "by_difficulty", "by_severity"):
        print(f"\n{axis[3:]}")
        for key, count in stats[axis].items():
            print(f"  {key:<20} {count:>4}")
    surface = stats["surface"]
    if surface["routes"]:
        ratio = surface["safe_per_planted"]
        print(f"\npublished surface: {surface['routes']} routes "
              f"({surface['planted']} planted, {surface['safe']} safe"
              + (f", {ratio:.1f} safe per planted" if ratio is not None else "") + ")")
    oracles = stats["oracles"]
    if oracles["without_signal"]:
        print(f"\n{len(oracles['without_signal'])} entr(ies) with no oracle.signal: "
              + ", ".join(oracles["without_signal"]))
    if stats["deception"]["without_decoy_note"]:
        print(f"{len(stats['deception']['without_decoy_note'])} entr(ies) with no decoy_note "
              "(cover story): " + ", ".join(stats["deception"]["without_decoy_note"]))

    empty = stats["empty_classes"]
    print(f"\n{len(empty)} class(es) with zero planted vulnerabilities:")
    for i in range(0, len(empty), 4):
        print("  " + "  ".join(f"{c:<28}" for c in empty[i:i + 4]).rstrip())
    return 0


# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bench", description=__doc__.splitlines()[0])
    parser.add_argument("--version", action="version", version=f"benchctl {__version__}")
    parser.add_argument("--root", help="repository root (default: auto-detected)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_validate = sub.add_parser("validate", help="check catalog integrity")
    p_validate.add_argument("--json", action="store_true")
    p_validate.set_defaults(func=cmd_validate)

    p_score = sub.add_parser("score", help="score one run against the catalog")
    p_score.add_argument("--run", required=True, help="run id")
    source = p_score.add_mutually_exclusive_group()
    source.add_argument("--collector", default=_DEFAULT_COLLECTOR, help="collector base URL")
    source.add_argument("--events", help="events JSON export instead of the collector")
    p_score.add_argument("--findings", help="normalised findings JSON for precision analysis")
    p_score.add_argument("--app-map", help='JSON map of URL authority -> app key')
    p_score.add_argument("--apps", help="comma-separated app keys in scope")
    p_score.add_argument("--tool", help="override the tool key recorded in the score")
    p_score.add_argument("--tool-version")
    p_score.add_argument("--profile")
    p_score.add_argument("--out", help="score document path")
    p_score.add_argument("--json", action="store_true", help="also print the document")
    p_score.add_argument("--ignore-catalog-errors", action="store_true")
    p_score.set_defaults(func=cmd_score)

    p_report = sub.add_parser("report", help="render N score documents")
    p_report.add_argument("--runs", required=True, help="comma-separated run ids or score.json paths")
    p_report.add_argument("--out", help="output directory (default: results/)")
    p_report.add_argument("--results-dir")
    p_report.set_defaults(func=cmd_report)

    p_catalog = sub.add_parser("catalog", help="catalog utilities")
    catalog_sub = p_catalog.add_subparsers(dest="catalog_command", required=True)
    p_stats = catalog_sub.add_parser("stats", help="coverage matrix and backlog")
    p_stats.add_argument("--json", action="store_true")
    p_stats.set_defaults(func=cmd_catalog_stats)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
