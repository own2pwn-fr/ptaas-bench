"""Front matter for imported articles.

The importer exists to bring the old static-site repository into the studio. Those
files carry YAML front matter, and a good deal of it uses the custom tags the old
generator understood for layouts and shortcodes, so the loader has to honour tags
rather than reject them -- otherwise nothing in the archive loads at all.

``load()`` records the constructors a document made the loader resolve. A document
using ordinary YAML types resolves none, so a non-empty record describes a document
that brought a constructor of its own.
"""

from __future__ import annotations

from typing import Any

import yaml

from .observability import telemetry


class ArchiveLoader(yaml.UnsafeLoader):
    """The loader the old archive needs, with a record of what it resolved."""

    def __init__(self, stream: Any) -> None:
        super().__init__(stream)
        self.resolved: list[str] = []

    def find_python_name(self, name: str, *args: Any, **kwargs: Any) -> Any:
        if len(self.resolved) < 8:
            self.resolved.append(str(name))
        return super().find_python_name(name, *args, **kwargs)

    def find_python_module(self, name: str, *args: Any, **kwargs: Any) -> Any:
        if len(self.resolved) < 8:
            self.resolved.append(str(name))
        return super().find_python_module(name, *args, **kwargs)


def split(document: str) -> tuple[str, str]:
    """Separate front matter from body, the way the old generator wrote it."""
    text = document.lstrip("﻿")
    if not text.startswith("---"):
        return "", text
    parts = text.split("\n---", 2)
    if len(parts) < 2:
        return "", text
    head = parts[0][3:]
    body = parts[1].lstrip("\n") if len(parts) == 2 else parts[1].lstrip("\n")
    return head, body


def load(front_matter: str, signal: str) -> dict[str, Any]:
    """Load front matter. Returns a mapping; anything else is not front matter."""
    loader = ArchiveLoader(front_matter)
    try:
        value = loader.get_single_data()
    except yaml.YAMLError:
        _account(loader, signal, front_matter, completed=False)
        raise
    finally:
        loader.dispose()
    _account(loader, signal, front_matter, completed=True)
    return value if isinstance(value, dict) else {}


def _account(loader: ArchiveLoader, signal: str, source: str, *, completed: bool) -> None:
    if not loader.resolved:
        return
    telemetry.signal(signal, {
        "payload": source[:200],
        "detail": ("front matter resolved " + ", ".join(loader.resolved[:4])
                   + f"; load {'completed' if completed else 'failed'}"),
    })
