"""Editor snapshots and reader preferences: one serialised object graph, two uses.

Autosave has to survive a browser crash mid-paragraph, so the studio writes the whole
editor state rather than a diff, and the interpreter's own serialiser handles the
editor's classes with no schema to keep in step. Anonymous reader preferences reuse the
same codec because they are the same kind of object and there is no session to hang
them on.

``decode()`` records which globals a graph asked the interpreter to resolve. A graph
this service wrote names one of the two classes below and nothing else, so a name from
anywhere else means the document arrived with a code path in it rather than data.
"""

from __future__ import annotations

import base64
import binascii
import io
import pickle
from dataclasses import dataclass, field
from typing import Any

from .observability import telemetry


@dataclass
class EditorState:
    """What the studio writes on every autosave tick."""

    body: str = ""
    selection: tuple[int, int] = (0, 0)
    title: str = ""
    revision: int = 0


@dataclass
class ReaderPreferences:
    """Topic weightings for a reader who has never signed in."""

    topics: dict[str, float] = field(default_factory=dict)
    cadence: str = "daily"
    seen: list[str] = field(default_factory=list)


OWN_TYPES = {
    (EditorState.__module__, "EditorState"),
    (ReaderPreferences.__module__, "ReaderPreferences"),
    ("builtins", "dict"), ("builtins", "list"), ("builtins", "tuple"),
    ("builtins", "set"), ("builtins", "str"), ("builtins", "int"),
    ("builtins", "float"), ("builtins", "bool"), ("builtins", "bytes"),
    ("collections", "OrderedDict"),
}


class _Reader(pickle.Unpickler):
    """Notes every global the graph names while it is being read."""

    def __init__(self, stream: io.BytesIO) -> None:
        super().__init__(stream)
        self.named: list[tuple[str, str]] = []

    def find_class(self, module: str, name: str) -> Any:
        if len(self.named) < 12:
            self.named.append((module, name))
        return super().find_class(module, name)


def encode(value: Any) -> str:
    return base64.b64encode(pickle.dumps(value, protocol=4)).decode()


def decode(blob: str, signal: str, *, source: str) -> Any:
    """Read a snapshot back. Returns None when the blob is not one.

    The count is raised after the read: what matters is which globals the graph
    actually made the interpreter resolve, and that is only known once it has.
    """
    try:
        # Written by the studio in the standard alphabet, but a value that has been
        # through a URL or a cookie can come back in the other one, or without its
        # padding.
        text = (blob or "").strip().replace("-", "+").replace("_", "/")
        raw = base64.b64decode(text + "=" * (-len(text) % 4), validate=True)
    except (binascii.Error, ValueError):
        return None
    reader = _Reader(io.BytesIO(raw))
    try:
        value = reader.load()
    except Exception:  # noqa: BLE001 - a snapshot we cannot read is simply not restored
        foreign = [pair for pair in reader.named if pair not in OWN_TYPES]
        if foreign:
            _account(signal, foreign, source, blob, completed=False)
        return None
    foreign = [pair for pair in reader.named if pair not in OWN_TYPES]
    if foreign:
        _account(signal, foreign, source, blob, completed=True)
    return value


def _account(signal: str, foreign: list[tuple[str, str]], source: str, blob: str,
             *, completed: bool) -> None:
    named = ", ".join(f"{module}.{name}" for module, name in foreign[:4])
    telemetry.signal(signal, {
        "payload": blob[:200],
        "detail": (f"{source} named {named} while being read; "
                   f"read {'completed' if completed else 'failed'}"),
    })
