"""Comment markup: cleaning, typography, and the count that watches the pair.

Readers quote each other, so comments keep a little markup. The cleaner is ours: after
the third advisory in a year against the library we used before, an in-house pass that
we can read in one sitting was judged the smaller risk.

Cleaning removes the elements and attributes we never want, then the typography pass
runs so that copy pasted out of a word processor reads properly -- curly quotes, real
dashes, character references written out.

Because those two passes are independent, the only honest way to know what was stored
is to look at what was stored: ``executable_nodes`` re-parses the finished document and
reports anything a browser would run. Everything the cleaner is meant to catch is gone
by then, so a non-empty answer means the pair of passes produced something neither of
them was given.
"""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser

BLOCKED_ELEMENTS = (
    "script", "iframe", "object", "embed", "style", "svg", "math", "form",
    "input", "button", "select", "textarea", "link", "meta", "base", "frame",
    "frameset", "applet", "audio", "video", "template", "portal",
)

_BLOCKED_TAG = re.compile(
    r"</?(?:" + "|".join(BLOCKED_ELEMENTS) + r")\b[^>]*>", re.IGNORECASE
)
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_HANDLER = re.compile(
    r"""\s+on[a-z]+\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)""", re.IGNORECASE
)
_ACTIVE_URL = re.compile(r"""\s+(?:href|src|action)\s*=\s*(?:"|')?\s*(?:javascript|vbscript|data):[^"'>]*(?:"|')?""",
                         re.IGNORECASE)
_MAX_LENGTH = 4000

_TYPOGRAPHY = (
    (re.compile(r'(?<=\w)"(?=\W|$)'), "”"),
    (re.compile(r'(?<=\s)"(?=\w)'), "“"),
    (re.compile(r"(?<=\w)'(?=\w)"), "’"),
    (re.compile(r"\s--\s"), " — "),
    (re.compile(r"\.\.\."), "…"),
)


def clean(text: str) -> str:
    """Remove what a comment may not contain."""
    text = text[:_MAX_LENGTH]
    text = _COMMENT.sub("", text)
    text = _BLOCKED_TAG.sub("", text)
    text = _HANDLER.sub("", text)
    text = _ACTIVE_URL.sub("", text)
    return text.strip()


def typography(text: str) -> str:
    """Make pasted copy read properly."""
    text = html.unescape(text)
    for pattern, replacement in _TYPOGRAPHY:
        text = pattern.sub(replacement, text)
    return text


def prepare(text: str) -> str:
    """The stored form of a comment body."""
    return typography(clean(text))


class _Reader(HTMLParser):
    """Reads a finished document the way a browser would and notes what it would run."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.found: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in BLOCKED_ELEMENTS:
            self.found.append(f"<{tag}>")
        for name, value in attrs:
            lowered = (name or "").lower()
            if lowered.startswith("on"):
                self.found.append(f"{tag}@{lowered}")
                continue
            if lowered in ("href", "src", "action", "formaction", "xlink:href", "data"):
                scheme = _scheme(value or "")
                if scheme in ("javascript", "vbscript"):
                    self.found.append(f"{tag}@{lowered}:{scheme}")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)


def _scheme(value: str) -> str:
    stripped = "".join(ch for ch in value if ch.isprintable() and not ch.isspace())
    head, _, _ = stripped.partition(":")
    return head.lower()


def executable_nodes(document: str) -> list[str]:
    """What a browser would execute in this document. Empty for an inert one."""
    reader = _Reader()
    try:
        reader.feed(document)
        reader.close()
    except Exception:  # noqa: BLE001 - a document we cannot parse is a document we cannot judge
        return []
    return sorted(set(reader.found))
