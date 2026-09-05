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
   the size of the cliff explicit instead of leaving it to be eyeballed.

Cells show ``recall% (hit/applicable)``. An empty cell renders as an em dash and
means "no planted vulnerability in that bucket", which is deliberately different
from ``0%``.
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
    return f"R {parts[0]} · E {parts[1]} · T {parts[2]}"


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
    header = ["tool", "vulns", "reach", "exercise", "trigger", "precision", "FP"]
    rows = []
    for doc in docs:
        overall = _node(doc, "metrics", "overall") or {}
        f = doc.get("findings") or {}
        precision = f.get("precision")
        rows.append([
            tool_name(doc),
            str((doc.get("catalog") or {}).get("vulns_in_scope", overall.get("vulns", 0))),
            _fmt(overall, "reach"),
            _fmt(overall, "exercise"),
            _fmt(overall, "trigger"),
            _DASH if precision is None else f"{precision * 100:.0f}%",
            _DASH if not f else str(f.get("false_positives", 0)),
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
    out.append(("severity", "By severity — trigger recall",
                *_axis_rows(docs, "by_severity", "trigger")))
    out.append(("auth", "By required credentials — trigger recall",
                *_axis_rows(docs, "by_auth", "trigger")))
    out.append(("difficulty", "By discovery difficulty — reach recall",
                *_axis_rows(docs, "by_difficulty", "reach")))
    out.append(("requires", "By required capability — reach recall",
                *_axis_rows(docs, "by_requires", "reach")))
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
            str(f.get("false_positives", 0)),
            str(f.get("duplicates", 0)),
            str(f.get("ambiguous", 0)),
            _DASH if f.get("precision") is None else f"{f['precision'] * 100:.0f}%",
            _DASH if f.get("precision_conservative") is None
            else f"{f['precision_conservative'] * 100:.0f}%",
        ])
    if fp_rows:
        parts.append("## Precision")
        parts.append("")
        parts.append(_md_table(
            ["tool", "findings", "TP", "FP", "dup", "ambiguous", "precision", "precision (conservative)"],
            fp_rows,
        ))
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
            str(f.get("false_positives", 0)), str(f.get("duplicates", 0)),
            str(f.get("ambiguous", 0)),
            _DASH if f.get("precision") is None else f"{f['precision'] * 100:.0f}% (—)",
            _DASH if f.get("precision_conservative") is None
            else f"{f['precision_conservative'] * 100:.0f}% (—)",
        ])
    if fp_rows:
        body.append("<h2>Precision</h2>")
        body.append(
            '<p class="legend">Two readings are published: <code>precision</code> ignores '
            "location-match/class-mismatch findings, <code>conservative</code> counts them "
            "as false positives.</p>"
        )
        body.append(_html_table(
            ["tool", "findings", "TP", "FP", "dup", "ambiguous", "precision", "conservative"],
            fp_rows,
        ))
        for doc in docs:
            f = doc.get("findings") or {}
            fps = f.get("false_positive_list") or []
            if not fps:
                continue
            body.append(f"<h2>False positives — {html.escape(tool_name(doc))}</h2>")
            body.append(_html_table(
                ["method", "url", "param", "cwe", "name", "reason"],
                [[str(r.get("method") or ""), str(r.get("url") or ""), str(r.get("param") or ""),
                  ",".join(str(c) for c in (r.get("cwe") or [])), str(r.get("name") or ""),
                  str(r.get("reason") or "")] for r in fps],
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
