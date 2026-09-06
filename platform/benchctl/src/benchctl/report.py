"""Rendering of one or many score documents.

Three outputs, one input format (the score document, see
``results/schema/score.schema.json``):

* :func:`markdown_report` -- comparison tables meant to be pasted into the repo
  README. Deterministic column order so a diff of two report runs is readable.
* :func:`html_report` -- a single self-contained file: no CDN, no external font,
  no JavaScript, inline CSS with a ``prefers-color-scheme`` block so it is legible
  on a dark laptop and on a projector. It must open from a USB stick years from
  now, because published benchmark results that depend on a live CDN are not
  reproducible.
* the raw JSON, copied verbatim.

Headline tables, in the order a reader should meet them:

1. summary (one row per tool),
2. tool x OWASP category, one table per edition, trigger recall -- the number that
   answers "what can this tool actually exploit",
3. tool x family with reach / exercise / trigger side by side -- this is where the
   funnel becomes visible: high reach with low trigger means a crawler,
4. tool x render mode -- SPA collapse. Reach is shown first here because the
   collapse happens at crawl time, and a ``static-html - SPA`` delta column makes
   the size of the cliff explicit instead of leaving it to be eyeballed,
5. crawl coverage over the whole published surface, next to the planted-only
   reach. Two denominators, both shown: planted-only flatters a tool that walks
   the pages we made attractive, surface coverage says whether it crawled the site.

Cells show ``recall% (hit/applicable)``. An empty cell renders as an em dash and
means "no planted vulnerability in that bucket", which is deliberately different
from ``0%``. Trigger columns are the headline (proof only); where low-confidence
out-of-band attributions exist they are shown as a separate ``+N weak`` suffix or
column, never folded into the headline.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

__all__ = [
    "load_score",
    "tool_name",
    "markdown_report",
    "html_report",
    "write_report",
]

_AXES = ("reach", "exercise", "trigger")
_COVERAGE_KEYS = ("surface", "planted_routes", "safe_routes")
_DASH = "—"


def load_score(path: Path | str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def tool_name(doc: Mapping[str, Any]) -> str:
    run = doc.get("run") or {}
    tool = run.get("tool") or "unknown"
    version = run.get("tool_version")
    profile = run.get("profile")
    label = f"{tool} {version}" if version else str(tool)
    if profile and profile not in {"default", ""}:
        label = f"{label} ({profile})"
    return label


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _cell(metric: Mapping[str, Any] | None, axis: str) -> tuple[float | None, int, int]:
    if not metric:
        return None, 0, 0
    block = metric.get(axis) or {}
    return block.get("recall"), int(block.get("hit") or 0), int(block.get("applicable") or 0)


def _fmt(metric: Mapping[str, Any] | None, axis: str) -> str:
    recall, hit, applicable = _cell(metric, axis)
    if recall is None or applicable == 0:
        return _DASH
    return f"{recall * 100:.0f}% ({hit}/{applicable})"


def _fmt_triplet(metric: Mapping[str, Any] | None) -> str:
    parts = []
    for axis in _AXES:
        recall, _, applicable = _cell(metric, axis)
        parts.append(_DASH if recall is None or applicable == 0 else f"{recall * 100:.0f}%")
    text = f"R {parts[0]} · E {parts[1]} · T {parts[2]}"
    # Weak out-of-band attributions are shown as a suffix, never merged into T.
    _, trig_hit, _ = _cell(metric, "trigger")
    _, any_hit, _ = _cell(metric, "trigger_any")
    if any_hit > trig_hit:
        text += f" (+{any_hit - trig_hit} weak)"
    return text


def _fmt_coverage(block: Mapping[str, Any] | None) -> str:
    if not block or not block.get("routes"):
        return _DASH
    coverage = block.get("coverage")
    if coverage is None:
        return _DASH
    return f"{coverage * 100:.0f}% ({block['covered']}/{block['routes']})"


def _union_keys(docs: Sequence[Mapping[str, Any]], *path: str) -> list[str]:
    keys: set[str] = set()
    for doc in docs:
        node: Any = doc
        for part in path:
            node = (node or {}).get(part) if isinstance(node, Mapping) else None
        if isinstance(node, Mapping):
            keys.update(node.keys())
    return sorted(keys)


def _node(doc: Mapping[str, Any], *path: str) -> Any:
    node: Any = doc
    for part in path:
        if not isinstance(node, Mapping):
            return None
        node = node.get(part)
    return node


def _editions(docs: Sequence[Mapping[str, Any]]) -> list[str]:
    return _union_keys(docs, "metrics", "by_owasp")


def _labels(docs: Sequence[Mapping[str, Any]], edition: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for doc in docs:
        legend = _node(doc, "legend", "owasp", edition)
        if isinstance(legend, Mapping):
            out.update({str(k): str(v) for k, v in legend.items()})
    return out


def _md_table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |",
             "|" + "|".join("---" for _ in header) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# table builders (shared by markdown and html)
# --------------------------------------------------------------------------- #

def _summary_rows(docs: Sequence[Mapping[str, Any]]) -> tuple[list[str], list[list[str]]]:
    header = ["tool", "scope", "vulns", "reach", "exercise", "trigger", "trigger +weak oob",
              "surface crawl", "precision (confirmed)", "FP confirmed", "outside corpus"]
    rows = []
    for doc in docs:
        overall = _node(doc, "metrics", "overall") or {}
        crawl = _node(doc, "metrics", "crawl") or {}
        f = doc.get("findings") or {}
        # Headline precision counts only false positives the inventory contradicts.
        precision = f.get("precision_confirmed")
        fp = _DASH if not f else str(f.get("false_positives_confirmed", 0))
        unscored = _DASH if not f else str(f.get("out_of_catalog", 0))
        scope = _node(doc, "scope", "apps") or (doc.get("run") or {}).get("targets") or []
        rows.append([
            tool_name(doc),
            ", ".join(scope) if scope else _DASH,
            str((doc.get("catalog") or {}).get("vulns_in_scope", overall.get("vulns", 0))),
            _fmt(overall, "reach"),
            _fmt(overall, "exercise"),
            _fmt(overall, "trigger"),
            _fmt(overall, "trigger_any"),
            _fmt_coverage(crawl.get("surface")),
            _DASH if precision is None else f"{precision * 100:.0f}%",
            fp,
            unscored,
        ])
    return header, rows


def _owasp_rows(docs: Sequence[Mapping[str, Any]], edition: str) -> tuple[list[str], list[list[str]]]:
    codes = sorted({
        code
        for doc in docs
        for code in (_node(doc, "metrics", "by_owasp", edition) or {})
    })
    header = ["tool", *codes]
    rows = []
    for doc in docs:
        cells = _node(doc, "metrics", "by_owasp", edition) or {}
        rows.append([tool_name(doc), *[_fmt(cells.get(c), "trigger") for c in codes]])
    return header, rows


def _family_rows(docs: Sequence[Mapping[str, Any]]) -> tuple[list[str], list[list[str]]]:
    families = _union_keys(docs, "metrics", "by_family")
    header = ["tool", *families]
    rows = []
    for doc in docs:
        cells = _node(doc, "metrics", "by_family") or {}
        rows.append([tool_name(doc), *[_fmt_triplet(cells.get(f)) for f in families]])
    return header, rows


def _render_rows(docs: Sequence[Mapping[str, Any]]) -> tuple[list[str], list[list[str]]]:
    modes = _union_keys(docs, "metrics", "by_render")
    header = ["tool", *modes, "static→SPA reach delta"]
    rows = []
    for doc in docs:
        cells = _node(doc, "metrics", "by_render") or {}
        body = []
        for mode in modes:
            reach, _, applicable = _cell(cells.get(mode), "reach")
            trig, _, _ = _cell(cells.get(mode), "trigger")
            if reach is None or applicable == 0:
                body.append(_DASH)
            else:
                trig_txt = _DASH if trig is None else f"{trig * 100:.0f}%"
                body.append(f"reach {reach * 100:.0f}% → trig {trig_txt}")
        rows.append([tool_name(doc), *body, _spa_delta(cells)])
    return header, rows


def _spa_delta(cells: Mapping[str, Any]) -> str:
    """static-html reach minus the mean SPA reach: the size of the cliff."""
    static, _, n_static = _cell(cells.get("static-html"), "reach")
    spa = [
        _cell(cells.get(mode), "reach")
        for mode in cells
        if str(mode).startswith("spa-")
    ]
    spa = [(r, h, a) for (r, h, a) in spa if r is not None and a]
    if static is None or not n_static or not spa:
        return _DASH
    hits = sum(h for _, h, _ in spa)
    total = sum(a for _, _, a in spa)
    return f"-{(static - hits / total) * 100:.0f} pts"


def _crawl_rows(docs: Sequence[Mapping[str, Any]]) -> tuple[list[str], list[list[str]]]:
    """Whole-surface coverage next to the planted-only reach, never instead of it."""
    modes = sorted({
        mode for doc in docs for mode in (_node(doc, "metrics", "crawl", "by_render") or {})
    })
    header = ["tool", "whole surface", "planted routes", "safe routes",
              *[f"surface: {m}" for m in modes], "planted-vuln reach"]
    rows = []
    for doc in docs:
        crawl = _node(doc, "metrics", "crawl") or {}
        by_render = crawl.get("by_render") or {}
        reach = crawl.get("planted_vuln_reach") or {}
        recall = reach.get("recall")
        rows.append([
            tool_name(doc),
            _fmt_coverage(crawl.get("surface")),
            _fmt_coverage(crawl.get("planted_routes")),
            _fmt_coverage(crawl.get("safe_routes")),
            *[_fmt_coverage(by_render.get(m)) for m in modes],
            _DASH if recall is None
            else f"{recall * 100:.0f}% ({reach.get('hit', 0)}/{reach.get('applicable', 0)})",
        ])
    return header, rows


def _weak_rows(docs: Sequence[Mapping[str, Any]]) -> tuple[list[str], list[list[str]]]:
    header = ["tool", "headline trigger", "incl. weak attribution",
              "credited only weakly", "unattributed callbacks"]
    rows = []
    for doc in docs:
        low = doc.get("low_confidence_triggers") or {}
        head = low.get("headline_trigger") or {}
        incl = low.get("inclusive_trigger") or {}

        def fmt(block):
            recall = block.get("recall")
            return (_DASH if recall is None
                    else f"{recall * 100:.0f}% ({block.get('hit', 0)}/{block.get('applicable', 0)})")

        rows.append([
            tool_name(doc), fmt(head), fmt(incl),
            ", ".join(low.get("credited_only_here") or []) or _DASH,
            str(len(low.get("unattributed_callbacks") or [])),
        ])
    return header, rows


def _provenance_rows(docs: Sequence[Mapping[str, Any]]) -> tuple[list[str], list[list[str]]]:
    """What a third party needs to re-run this number and trust the comparison."""
    header = ["tool", "run", "images", "state reset", "container map"]
    rows = []
    for doc in docs:
        run = doc.get("run") or {}
        images = run.get("images") or {}
        resets = run.get("reset_digests") or {}
        consistent = run.get("reset_consistent")
        if consistent is None:
            reset_text = _DASH
        elif consistent:
            reset_text = f"clean ({len(resets)})"
        else:
            dirty = sorted(a for a, r in resets.items() if r.get("match") is False)
            reset_text = "DIRTY: " + ", ".join(dirty)
        rows.append([
            tool_name(doc),
            str(run.get("run_id") or _DASH),
            ", ".join(f"{app}@{digest[:19]}" for app, digest in sorted(images.items())) or _DASH,
            reset_text,
            "yes" if run.get("container_map_available") else "no",
        ])
    return header, rows


def _has_provenance(docs: Sequence[Mapping[str, Any]]) -> bool:
    return any(
        (doc.get("run") or {}).get("images")
        or (doc.get("run") or {}).get("reset_digests")
        or (doc.get("run") or {}).get("container_map_available")
        for doc in docs
    )


def _has_crawl(docs: Sequence[Mapping[str, Any]]) -> bool:
    return any(_node(doc, "metrics", "crawl", "inventory_available") for doc in docs)


def _has_weak(docs: Sequence[Mapping[str, Any]]) -> bool:
    return any(
        (doc.get("low_confidence_triggers") or {}).get("count")
        or (doc.get("low_confidence_triggers") or {}).get("unattributed_callbacks")
        for doc in docs
    )


def _sections(docs: Sequence[Mapping[str, Any]]) -> list[tuple[str, str, list[str], list[list[str]]]]:
    """(anchor, title, header, rows) for every headline table, in reading order."""
    out = [("summary", "Overall", *_summary_rows(docs))]
    for edition in _editions(docs):
        labels = _labels(docs, edition)
        legend = "; ".join(f"{c}={labels[c]}" for c in sorted(labels)) if labels else ""
        header, rows = _owasp_rows(docs, edition)
        out.append((f"owasp-{edition}",
                    f"OWASP Top 10 {edition} — trigger recall" + (f"\n{legend}" if legend else ""),
                    header, rows))
    out.append(("family", "By family — reach / exercise / trigger", *_family_rows(docs)))
    out.append(("render", "By rendering mode — where SPA crawling collapses", *_render_rows(docs)))
    if _has_crawl(docs):
        note = ("Planted-only reach counts just the pages we made attractive; surface "
                "coverage counts every route the target declares, safe ones included. "
                "Both are shown; neither replaces the other.")
        collapsed = max(
            (_node(doc, "metrics", "crawl", "rows_sharing_a_route_across_hosts") or 0)
            for doc in docs
        )
        if collapsed and any(
            _node(doc, "metrics", "crawl", "host_resolution") == "collapsed" for doc in docs
        ):
            # Say it in the table's own legend: on a multi-vhost target one visit
            # credits every row sharing that route, so surface coverage is an upper
            # bound rather than a measurement.
            note += (f" Requests carry no virtual host, and {collapsed} inventory rows share "
                     "a route across hosts, so surface coverage is an upper bound for them.")
        out.append(("crawl", "Crawl coverage — whole published surface\n" + note,
                    *_crawl_rows(docs)))
    if _has_weak(docs):
        out.append(("weak",
                    "Out-of-band attribution strength\n"
                    "Callbacks the sinkhole could only tie to a vulnerability by container and "
                    "time window are counted here and excluded from the headline trigger recall.",
                    *_weak_rows(docs)))
    out.append(("severity", "By severity — trigger recall",
                *_axis_rows(docs, "by_severity", "trigger")))
    out.append(("auth", "By required credentials — trigger recall",
                *_axis_rows(docs, "by_auth", "trigger")))
    out.append(("difficulty", "By discovery difficulty — reach recall",
                *_axis_rows(docs, "by_difficulty", "reach")))
    out.append(("requires", "By required capability — reach recall",
                *_axis_rows(docs, "by_requires", "reach")))
    if _has_provenance(docs):
        out.append(("provenance",
                    "Run provenance\n"
                    "The image digest actually running and the seeded-state digest read before "
                    "and after the run. A run whose state reset is DIRTY did not come back to "
                    "its seeded state, so the next run measured a different application.",
                    *_provenance_rows(docs)))
    return out


def _axis_rows(
    docs: Sequence[Mapping[str, Any]], group: str, axis: str
) -> tuple[list[str], list[list[str]]]:
    keys = _union_keys(docs, "metrics", group)
    header = ["tool", *keys]
    rows = []
    for doc in docs:
        cells = _node(doc, "metrics", group) or {}
        rows.append([tool_name(doc), *[_fmt(cells.get(k), axis) for k in keys]])
    return header, rows


# --------------------------------------------------------------------------- #
# markdown
# --------------------------------------------------------------------------- #

def markdown_report(docs: Sequence[Mapping[str, Any]]) -> str:
    docs = list(docs)
    parts: list[str] = ["# ptaas-bench results", ""]
    digests = {(_node(d, "catalog", "digest") or "?") for d in docs}
    parts.append(
        f"Catalog digest: {', '.join(sorted(digests))} · "
        f"{len(docs)} run(s) · cells are `recall% (hit/applicable)`, "
        f"`{_DASH}` means no planted vulnerability in that bucket."
    )
    parts.append("")
    for _anchor, title, header, rows in _sections(docs):
        head, _, legend = title.partition("\n")
        parts.append(f"## {head}")
        if legend:
            parts.append(f"<sub>{legend}</sub>")
        parts.append("")
        parts.append(_md_table(header, rows))
        parts.append("")

    fp_rows = []
    for doc in docs:
        f = doc.get("findings") or {}
        if not f:
            continue
        fp_rows.append([
            tool_name(doc),
            str(f.get("total", 0)),
            str(f.get("true_positives", 0)),
            str(f.get("false_positives_confirmed", 0)),
            str(f.get("false_positives_unknown_route", 0)),
            str(f.get("out_of_catalog", 0)),
            str(f.get("duplicates", 0)),
            str(f.get("ambiguous", 0)),
            _DASH if f.get("precision_confirmed") is None
            else f"{f['precision_confirmed'] * 100:.0f}%",
            _DASH if f.get("precision") is None else f"{f['precision'] * 100:.0f}%",
            _DASH if f.get("precision_conservative") is None
            else f"{f['precision_conservative'] * 100:.0f}%",
        ])
    if fp_rows:
        parts.append("## Precision")
        parts.append(
            "<sub>The headline is `precision (confirmed)`: its denominator counts only "
            "false positives the route inventory contradicts. `FP unconfirmable` sits on "
            "paths we never declared, or that only a catch-all pattern row matched — our "
            "gap, not the tool's error — "
            "and is counted separately in `precision (all)`. `outside corpus` are real "
            "findings for classes this corpus does not plant (a missing CSP header, say): "
            "unscoreable, not wrong, and excluded from every denominator. `conservative` "
            "additionally counts right-place-wrong-class findings against the tool.</sub>")
        parts.append("")
        parts.append(_md_table(
            ["tool", "findings", "TP", "FP confirmed", "FP unconfirmable", "outside corpus",
             "dup", "ambiguous", "precision (confirmed)", "precision (all)", "conservative"],
            fp_rows,
        ))
        parts.append("")
        for doc in docs:
            f = doc.get("findings") or {}
            by_cwe = f.get("out_of_catalog_by_cwe") or {}
            if not by_cwe:
                continue
            parts.append(f"<sub>{tool_name(doc)} — outside the corpus: " + "; ".join(
                f"CWE-{cwe} ×{v['count']} ({v['reason']})" for cwe, v in by_cwe.items()
            ) + "</sub>")
            parts.append("")

    warn_rows = [
        [tool_name(doc), w.get("code", ""), w.get("vuln_id") or "", w.get("message", "")]
        for doc in docs
        for w in (doc.get("warnings") or [])
    ]
    if warn_rows:
        parts.append("## Instrumentation warnings")
        parts.append("")
        parts.append(_md_table(["tool", "code", "vuln", "message"], warn_rows))
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


# --------------------------------------------------------------------------- #
# html
# --------------------------------------------------------------------------- #

_CSS = """
:root {
  color-scheme: light dark;
  --bg: #ffffff; --fg: #16181d; --muted: #5b6270; --line: #d8dce4;
  --head: #f2f4f8; --accent: #4c6ef5; --bar: #dfe4ef; --warn: #b45309;
  --good: #0f7b4f; --bad: #b42318;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #101317; --fg: #e8ebf0; --muted: #9aa3b2; --line: #2a3038;
    --head: #171b21; --accent: #8da2fb; --bar: #232a33; --warn: #f0b429;
    --good: #45c08a; --bad: #f27272;
  }
}
* { box-sizing: border-box; }
body { margin: 0; padding: 2rem 1.5rem 4rem; background: var(--bg); color: var(--fg);
  font: 15px/1.55 -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; }
main { max-width: 1400px; margin: 0 auto; }
h1 { font-size: 1.6rem; margin: 0 0 .25rem; }
h2 { font-size: 1.1rem; margin: 2.4rem 0 .3rem; padding-bottom: .3rem;
  border-bottom: 1px solid var(--line); }
p.sub, .legend { color: var(--muted); font-size: .82rem; margin: .2rem 0 1rem; }
.wrap { overflow-x: auto; border: 1px solid var(--line); border-radius: 8px; }
table { border-collapse: collapse; width: 100%; font-size: .85rem; }
th, td { padding: .45rem .6rem; text-align: left; border-bottom: 1px solid var(--line);
  white-space: nowrap; }
thead th { background: var(--head); position: sticky; top: 0; font-weight: 600; }
tbody tr:last-child td { border-bottom: none; }
td.tool, th.tool { font-weight: 600; white-space: nowrap; }
.bar { position: relative; display: block; min-width: 92px; padding: .1rem .3rem;
  border-radius: 4px; background: var(--bar); }
.bar > i { position: absolute; inset: 0 auto 0 0; border-radius: 4px;
  background: var(--accent); opacity: .35; }
.bar > span { position: relative; font-variant-numeric: tabular-nums; }
.na { color: var(--muted); }
.pill { display: inline-block; padding: 0 .4rem; border-radius: 999px;
  border: 1px solid var(--line); font-size: .75rem; }
.warn { color: var(--warn); }
.good { color: var(--good); }
.bad { color: var(--bad); }
footer { margin-top: 3rem; color: var(--muted); font-size: .78rem; }
ul.warnings { font-size: .82rem; color: var(--muted); padding-left: 1.1rem; }
"""


def _html_cell(text: str) -> str:
    """Render a cell, drawing an inline bar when the text starts with a percentage."""
    if text == _DASH:
        return '<td class="na">—</td>'
    pct = None
    if text.endswith(")") and "%" in text:
        try:
            pct = float(text.split("%", 1)[0])
        except ValueError:
            pct = None
    if pct is None:
        return f"<td>{html.escape(text)}</td>"
    return (
        f'<td><span class="bar"><i style="width:{max(0.0, min(100.0, pct)):.0f}%"></i>'
        f"<span>{html.escape(text)}</span></span></td>"
    )


def _html_table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    head = "".join(f"<th>{html.escape(h)}</th>" for h in header)
    body = []
    for row in rows:
        cells = [f'<td class="tool">{html.escape(str(row[0]))}</td>']
        cells += [_html_cell(str(c)) for c in row[1:]]
        body.append("<tr>" + "".join(cells) + "</tr>")
    return (
        '<div class="wrap"><table><thead><tr>' + head + "</tr></thead><tbody>"
        + "".join(body) + "</tbody></table></div>"
    )


def html_report(docs: Sequence[Mapping[str, Any]]) -> str:
    docs = list(docs)
    generated = ", ".join(sorted({str(d.get("generated_at")) for d in docs}))
    digests = ", ".join(sorted({str(_node(d, "catalog", "digest")) for d in docs}))
    body: list[str] = [
        "<h1>ptaas-bench results</h1>",
        f'<p class="sub">{len(docs)} run(s) · catalog digest {html.escape(digests)} · '
        f"generated {html.escape(generated)} · cells show <code>recall% (hit/applicable)</code>; "
        "<span class=\"na\">—</span> means no planted vulnerability in that bucket.</p>",
    ]
    for _anchor, title, header, rows in _sections(docs):
        head, _, legend = title.partition("\n")
        body.append(f"<h2>{html.escape(head)}</h2>")
        if legend:
            body.append(f'<p class="legend">{html.escape(legend)}</p>')
        body.append(_html_table(header, rows))

    fp_rows = []
    for doc in docs:
        f = doc.get("findings") or {}
        if not f:
            continue
        fp_rows.append([
            tool_name(doc), str(f.get("total", 0)), str(f.get("true_positives", 0)),
            str(f.get("false_positives_confirmed", 0)),
            str(f.get("false_positives_unknown_route", 0)),
            str(f.get("out_of_catalog", 0)),
            str(f.get("duplicates", 0)), str(f.get("ambiguous", 0)),
            _DASH if f.get("precision_confirmed") is None
            else f"{f['precision_confirmed'] * 100:.0f}% (—)",
            _DASH if f.get("precision") is None else f"{f['precision'] * 100:.0f}% (—)",
            _DASH if f.get("precision_conservative") is None
            else f"{f['precision_conservative'] * 100:.0f}% (—)",
        ])
    if fp_rows:
        body.append("<h2>Precision</h2>")
        body.append(
            '<p class="legend">The headline is <code>precision (confirmed)</code>: only '
            "false positives the route inventory contradicts are in its denominator. Findings "
            "on paths the target serves but never declared are our gap, not the tool's error, "
            "and are counted separately. <code>outside corpus</code> findings are real findings "
            "for classes this corpus does not plant — unscoreable, not wrong — and are excluded "
            "from every denominator.</p>"
        )
        body.append(_html_table(
            ["tool", "findings", "TP", "FP confirmed", "FP unconfirmable", "outside corpus",
             "dup", "ambiguous", "precision (confirmed)", "precision (all)", "conservative"],
            fp_rows,
        ))
        for doc in docs:
            f = doc.get("findings") or {}
            outside = f.get("out_of_catalog_list") or []
            if outside:
                body.append(f"<h2>Outside the corpus — {html.escape(tool_name(doc))}</h2>")
                body.append('<p class="legend">Real findings for classes this benchmark does '
                            "not plant. Listed so a reader can check that judgement, and "
                            "counted against nobody.</p>")
                body.append(_html_table(
                    ["method", "url", "cwe", "name", "reason"],
                    [[str(r.get("method") or ""), str(r.get("url") or ""),
                      ",".join(str(c) for c in (r.get("cwe") or [])),
                      str(r.get("name") or ""), str(r.get("reason") or "")] for r in outside],
                ))
            fps = f.get("false_positive_list") or []
            if not fps:
                continue
            body.append(f"<h2>False positives — {html.escape(tool_name(doc))}</h2>")
            body.append(_html_table(
                ["method", "url", "param", "cwe", "name", "basis", "reason"],
                [[str(r.get("method") or ""), str(r.get("url") or ""), str(r.get("param") or ""),
                  ",".join(str(c) for c in (r.get("cwe") or [])), str(r.get("name") or ""),
                  str(r.get("fp_basis") or "—"), str(r.get("reason") or "")] for r in fps],
            ))

    warnings = [(tool_name(doc), w) for doc in docs for w in (doc.get("warnings") or [])]
    if warnings:
        body.append("<h2>Instrumentation warnings</h2>")
        body.append(
            '<p class="legend">These are platform problems, not tool problems: they mean an '
            "SDK under-reported something we had to compensate for.</p>"
        )
        body.append('<ul class="warnings">' + "".join(
            f'<li><span class="pill">{html.escape(tool)}</span> '
            f'<span class="warn">{html.escape(str(w.get("code")))}</span> '
            f'{html.escape(str(w.get("vuln_id") or ""))} — {html.escape(str(w.get("message")))}</li>'
            for tool, w in warnings
        ) + "</ul>")

    return (
        "<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        "<title>ptaas-bench results</title><style>" + _CSS + "</style></head><body><main>"
        + "".join(body)
        + '<footer>Generated by benchctl. Self-contained: no network access required. '
          "Recall is measured inside the targets; precision is judged against the catalog "
          "with the rules documented in benchctl/findings.py.</footer>"
        "</main></body></html>\n"
    )


def write_report(docs: Sequence[Mapping[str, Any]], out_dir: Path | str) -> list[Path]:
    """Write results.md, results.html and results.json into ``out_dir``."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    md = out / "results.md"
    md.write_text(markdown_report(docs), encoding="utf-8")
    written.append(md)

    page = out / "results.html"
    page.write_text(html_report(docs), encoding="utf-8")
    written.append(page)

    raw = out / "results.json"
    raw.write_text(
        json.dumps({"runs": list(docs)}, indent=2, sort_keys=False) + "\n", encoding="utf-8"
    )
    written.append(raw)
    return written
