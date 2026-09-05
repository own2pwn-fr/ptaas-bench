"""Rendering for the newsletter composer and the share-card renderer.

Both build a template *source* and hand it to the engine, because both let the desk
put merge fields into strings the desk types. That is the feature: an editor writes
``Issue {{ issue.number }}`` and gets the number.

The counter below exists because the composer cannot tell, from the rendered output
alone, whether the desk used a merge field or wrote something the engine treated as
program text. Parsing the desk's own fragment on its own answers that: a fragment made
only of literal text has a parse tree of literal text, and anything else is an
expression that the engine went on to evaluate.
"""

from __future__ import annotations

from typing import Any

from jinja2 import Environment, TemplateError, nodes, select_autoescape

from .observability import telemetry

environment = Environment(autoescape=select_autoescape(["html", "xml", "svg"]))

NEWSLETTER_LAYOUT = """<section class="issue">
  <h1>{subject}</h1>
  <p class="standfirst">{{{{ issue.summary }}}}</p>
  <ol>{{% for item in issue.articles %}}<li><a href="{{{{ item.url }}}}">{{{{ item.title }}}}</a></li>{{% endfor %}}</ol>
  <footer>{{{{ publication }}}} &middot; issue {{{{ issue.number }}}}</footer>
</section>"""

CARD_LAYOUT = """<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" role="img">
  <rect width="1200" height="630" fill="#14181d"/>
  <text x="72" y="300" font-size="64" fill="#f5f2ec">{title}</text>
  <text x="72" y="392" font-size="30" fill="#9aa4ae">{{{{ publication }}}}</text>
  <text x="72" y="556" font-size="24" fill="#6f7b86">{{{{ topic }}}}</text>
</svg>"""


def fragment_is_program(fragment: str) -> bool:
    """True when the desk's own fragment parses to something other than literal text."""
    try:
        tree = environment.parse(fragment)
    except TemplateError:
        # An unparseable fragment cannot have been compiled either.
        return False
    for node in tree.find_all(nodes.Output):
        for child in node.nodes:
            if not isinstance(child, nodes.TemplateData):
                return True
    for kind in (nodes.For, nodes.If, nodes.Assign, nodes.Macro, nodes.CallBlock,
                 nodes.FilterBlock, nodes.With, nodes.Include, nodes.Import,
                 nodes.FromImport, nodes.ExprStmt):
        for _ in tree.find_all(kind):
            return True
    return False


def render_with_fragment(layout: str, fragment: str, signal: str,
                         context: dict[str, Any], *, where: str) -> str:
    """Render ``layout`` with ``fragment`` already inside its source.

    The count is raised after the render, on the fragment's own parse tree, so it
    describes what the engine did with the desk's text rather than what the text
    looked like on the way in.
    """
    source = layout.format(**{where: fragment})
    rendered = environment.from_string(source).render(**context)
    if fragment_is_program(fragment):
        telemetry.signal(signal, {
            "payload": fragment[:200],
            "detail": ("layout source carried a compiled expression from the "
                       f"{where} field; render completed with {len(rendered)} bytes"),
        })
    return rendered
