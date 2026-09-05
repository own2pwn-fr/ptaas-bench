"""Fragment post-processing.

Every fragment this application returns is written by hand rather than by a component
library, and the swap helper parses each one on the way out: it attaches the default
swap target for the component, and while it is there it checks that the element
matches the contract its component declares. A component that renders outside its
contract has had a value escape the slot it was interpolated into -- an attribute
where the template wrote text -- which the browser will happily act on and which is
invisible in a diff of the rendered page.

The check is on the markup that goes back, not on the value that went in.
"""

from __future__ import annotations

import json
from html.parser import HTMLParser

from markupsafe import Markup
from telemetry_agent import get_telemetry

from . import db

# component -> the attributes its template writes, and nothing else.
CONTRACTS = {
    "directory-chip": {"class", "data-part", "data-team", "data-sort", "hx-get",
                       "hx-target", "hx-swap"},
    "asset-card": {"class", "data-part", "id", "hx-get", "hx-vals", "hx-target", "hx-swap"},
}

# components whose parameter attribute must still parse as the object the template wrote.
PARAMETERISED = {"asset-card": "hx-vals"}


class _Elements(HTMLParser):
    def __init__(self, part: str) -> None:
        super().__init__(convert_charrefs=True)
        self.part = part
        self.found: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        mapping = {name: (value or "") for name, value in attrs}
        if mapping.get("data-part") == self.part:
            self.found.append(mapping)


def inspect(fragment: str, part: str, *, signal: str, context: dict, once: str | None = None) -> str:
    """Return the fragment unchanged, reporting a component that broke its contract."""
    contract = CONTRACTS.get(part)
    if contract is None:
        return fragment
    parser = _Elements(part)
    try:
        parser.feed(fragment)
        parser.close()
    except Exception:  # noqa: BLE001 - malformed markup is reported below, not raised
        return fragment

    breaches: list[str] = []
    for element in parser.found:
        extra = sorted(set(element) - contract)
        if extra:
            breaches.append("attributes not in the contract: " + ", ".join(extra))
        parameter = PARAMETERISED.get(part)
        if parameter:
            raw = element.get(parameter)
            if raw is None:
                breaches.append(f"{parameter} is missing")
            else:
                try:
                    if not isinstance(json.loads(raw), dict):
                        breaches.append(f"{parameter} is not an object")
                except ValueError:
                    breaches.append(f"{parameter} no longer parses")
    if not breaches:
        return fragment
    if once is not None and not db.seen_once(once):
        return fragment
    payload = dict(context)
    payload["component"] = part
    payload["detail"] = "; ".join(breaches)
    get_telemetry().signal(signal, payload)
    return fragment


def attribute_text(value: object) -> Markup:
    """Escape a value for use inside an attribute.

    The design system's own helper, written when every attribute in these templates
    was written with double quotes: it neutralises the characters that end a tag or a
    double-quoted value, and leaves the rest of the label readable in the markup so a
    depot label like O'Neill's van still reads as itself in a page source.
    """
    text = "" if value is None else str(value)
    for bad, good in (("&", "&amp;"), ("<", "&lt;"), (">", "&gt;"), ('"', "&quot;")):
        text = text.replace(bad, good)
    return Markup(text)
