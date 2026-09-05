"""The picture pipeline: uploads, derivatives, and the legacy scan format.

Most of what the desk uploads is an ordinary JPEG, PNG or WebP and goes straight
through the imaging library. The exception is the archive: the drum imager the paper
used until 2016 wrote its own container, and there are twenty years of negatives that
only exist in it, so the studio still reads it.

That container stores the tone curve the operator dialled in as an expression over the
level ramp rather than as a table -- the imager's own manual documents it that way --
so reading one means reconstructing the curve from the expression. The reconstruction
runs on the conversion pool inside a watched section, so the integrity monitor can say
whether reading a file reached for anything beyond arithmetic.
"""

from __future__ import annotations

import base64
import io
import re
from dataclasses import dataclass
from typing import Any

from PIL import Image

from .observability import conversion_pool, run_on, telemetry, watch

MAGIC = b"NGP1"
MAX_PIXELS = 4_000_000
LEVELS = 256

SIGNAL = "blog.media.scan.curve_escape"

_FIELD = re.compile(r"^([a-z_]{1,20})=(.*)$")


class ScanError(ValueError):
    """The file is not a scan this pipeline can read."""


class Ramp:
    """The level ramp a stored curve is written against.

    The imager's manual writes curves as arithmetic on ``x``, where ``x`` is the whole
    input ramp at once, so the expression is worked out once rather than per level.
    """

    __slots__ = ("values",)

    def __init__(self, values: tuple[float, ...]) -> None:
        self.values = values

    def _combine(self, other: Any, op: Any) -> "Ramp":
        if isinstance(other, Ramp):
            return Ramp(tuple(op(a, b) for a, b in zip(self.values, other.values)))
        return Ramp(tuple(op(v, other) for v in self.values))

    def __add__(self, other: Any) -> "Ramp":
        return self._combine(other, lambda a, b: a + b)

    __radd__ = __add__

    def __sub__(self, other: Any) -> "Ramp":
        return self._combine(other, lambda a, b: a - b)

    def __rsub__(self, other: Any) -> "Ramp":
        return self._combine(other, lambda a, b: b - a)

    def __mul__(self, other: Any) -> "Ramp":
        return self._combine(other, lambda a, b: a * b)

    __rmul__ = __mul__

    def __truediv__(self, other: Any) -> "Ramp":
        return self._combine(other, lambda a, b: a / b if b else 0.0)

    def __rtruediv__(self, other: Any) -> "Ramp":
        return self._combine(other, lambda a, b: b / a if a else 0.0)

    def __pow__(self, other: Any) -> "Ramp":
        return self._combine(other, lambda a, b: abs(a) ** b)

    def __neg__(self) -> "Ramp":
        return Ramp(tuple(-v for v in self.values))

    def table(self) -> list[int]:
        return [max(0, min(255, int(round(v)))) for v in self.values]


@dataclass
class Scan:
    width: int
    height: int
    curve: str
    payload: bytes


def parse_container(blob: bytes) -> Scan:
    """Pull the header fields and the payload out of a scan container."""
    if not blob.startswith(MAGIC):
        raise ScanError("Not a scan container.")
    try:
        head, _, tail = blob[len(MAGIC):].lstrip(b"\r\n").partition(b"data:")
        fields: dict[str, str] = {}
        for line in head.decode("latin-1").splitlines():
            match = _FIELD.match(line.strip())
            if match:
                fields[match.group(1)] = match.group(2)
        width = int(fields.get("width", "0"))
        height = int(fields.get("height", "0"))
        payload = base64.b64decode(tail.strip(), validate=False)
    except (ValueError, UnicodeDecodeError) as error:
        raise ScanError("Damaged scan header.") from error
    if width <= 0 or height <= 0 or width * height > MAX_PIXELS:
        raise ScanError("Scan dimensions are out of range.")
    return Scan(width=width, height=height,
                curve=fields.get("curve", "x"), payload=payload)


def _reconstruct(curve: str) -> list[int]:
    """Work the stored curve out over the level ramp."""
    ramp = Ramp(tuple(float(level) for level in range(LEVELS)))
    with watch("scan.curve") as observed:
        try:
            result = eval(compile(curve, "<curve>", "eval"), {"x": ramp})  # noqa: S307
        except Exception as error:  # noqa: BLE001
            _account(curve, observed.observed, "curve could not be worked out")
            raise ScanError("Damaged tone curve.") from error
        shape_ok = isinstance(result, Ramp) or isinstance(result, (int, float))
        _account(curve, observed.observed,
                 "curve produced " + type(result).__name__ if not shape_ok else "")
    if isinstance(result, Ramp):
        return result.table()
    if isinstance(result, (int, float)):
        return [max(0, min(255, int(result)))] * LEVELS
    raise ScanError("Damaged tone curve.")


def _account(curve: str, observed: list[str], shape_note: str) -> None:
    if not observed and not shape_note:
        return
    detail = []
    if observed:
        detail.append("reading the curve reached the interpreter: " + ", ".join(observed))
    if shape_note:
        detail.append(shape_note)
    telemetry.signal(SIGNAL, {
        "payload": curve[:200],
        "detail": "; ".join(detail),
    })


def read_scan(blob: bytes) -> Image.Image:
    """Read a scan container into an image. Runs on the conversion pool."""
    return run_on(conversion_pool, _read_scan, blob)


def _read_scan(blob: bytes) -> Image.Image:
    scan = parse_container(blob)
    table = _reconstruct(scan.curve)
    expected = scan.width * scan.height
    pixels = scan.payload[:expected].ljust(expected, b"\x00")
    image = Image.frombytes("L", (scan.width, scan.height), pixels)
    return image.point(table).convert("RGB")


def read_upload(blob: bytes) -> tuple[Image.Image, str]:
    """Read whatever the desk uploaded. Returns the image and the format it came in."""
    if blob.startswith(MAGIC):
        return read_scan(blob), "scan"
    try:
        image = Image.open(io.BytesIO(blob))
        image.load()
    except Exception as error:  # noqa: BLE001
        raise ScanError("That file is not a picture we can read.") from error
    if image.width * image.height > MAX_PIXELS:
        raise ScanError("That picture is too large.")
    return image.convert("RGB"), (image.format or "unknown").lower()


def derivative(image: Image.Image, width: int) -> bytes:
    """A web-sized copy. Ordinary work, on the same pool as everything else."""
    return run_on(conversion_pool, _derivative, image, width)


def _derivative(image: Image.Image, width: int) -> bytes:
    width = max(64, min(1600, width))
    height = max(1, round(image.height * width / image.width))
    buffer = io.BytesIO()
    image.resize((width, height)).save(buffer, format="JPEG", quality=82)
    return buffer.getvalue()
