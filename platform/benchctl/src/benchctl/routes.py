"""Route template normalisation.

This module is deliberately tiny and heavily tested because it is load-bearing for
the whole benchmark: every REACH decision, and therefore every EXERCISE decision,
goes through it. The targets are written in five languages and each framework
prints its route templates in its own dialect, while the catalog is written by a
human in one dialect. If the normaliser is wrong, a perfectly good scan scores 0.

Dialects folded into one canonical form:

    /api/orders/:id          Express, Sinatra, Rails, Laravel(:id in some routers)
    /api/orders/{id}         OpenAPI, Spring, JAX-RS, Laravel, Go chi, ASP.NET
    /api/orders/<int:id>     Flask / Werkzeug (typed converters)
    /api/orders/{id:int}     ASP.NET route constraints, Symfony-style requirements
    /api/orders/*            catch-all written as a bare star

All five normalise to ``/api/orders/{}`` and therefore compare equal.

Greedy (multi-segment) placeholders are kept distinct as ``{**}`` because they are
semantically different -- ``/files/<path:p>`` really does swallow several segments:

    /files/<path:p>   /files/{*p}   /files/{**p}   /files/{p...}   /files/[...slug]

Two comparison functions, with different strictness, on purpose:

* :func:`routes_equal` -- template vs template. A single placeholder matches a
  placeholder, never a literal. This is what REACH uses, because the SDK contract
  says the collector reports the *registered route template*. Being strict here
  means an SDK that regresses to concrete paths under-reports (we lose recall)
  rather than over-reports (we invent recall). Under-reporting is the safe failure
  for a benchmark that publishes numbers about other people's tools.

* :func:`route_matches_path` -- template vs concrete path. A placeholder matches
  any single literal segment. This is what the false-positive matcher uses, since
  third-party findings files carry concrete URLs and nothing else.

Other normalisations applied, and why:

* case folding -- route templates are re-emitted by the framework, and casing drift
  (``/api/Orders/{id}`` in ASP.NET vs ``/api/orders/:id`` in the catalog) is a
  reporting artefact of the framework, not a different endpoint. Benchmark targets
  use lowercase paths by convention, so folding cannot merge two real endpoints.
* duplicate slashes collapsed, trailing slash dropped (``/a/`` == ``/a``), query
  string and fragment dropped, surrounding whitespace dropped.
* the literal sentinel ``<unmatched>`` (what the SDKs report when a request hit no
  registered route) is preserved verbatim. It looks exactly like a Flask parameter
  and would otherwise normalise to ``{}``, i.e. every 404 would credit reach on
  every single-segment route in the catalog. That bug would be invisible and would
  inflate every score, hence the explicit guard and a dedicated test.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

__all__ = [
    "PLACEHOLDER",
    "GREEDY",
    "UNMATCHED",
    "normalize_route",
    "route_segments",
    "routes_equal",
    "route_matches_path",
    "path_from_url",
]

PLACEHOLDER = "{}"
GREEDY = "{**}"
UNMATCHED = "<unmatched>"

# Embedded placeholders inside an otherwise literal segment, e.g. "file-{id}.json"
# (Spring, chi) or "report-<int:n>.csv" (Flask). Rare but cheap to support.
_EMBEDDED_BRACE = re.compile(r"\{[^{}/]*\}")
_EMBEDDED_ANGLE = re.compile(r"<[^<>/]*>")


_GROUP_OPEN = {"{": "}", "<": ">", "[": "]"}


def _strip_query(path: str) -> str:
    """Drop the query string, but only at a `?` that is not inside a placeholder.

    Optional-parameter syntaxes (`{name?}`, `:name?`) contain a literal question
    mark, so a naive ``split("?")`` truncates the route template mid-parameter.
    """
    depth = 0
    for i, ch in enumerate(path):
        if ch in _GROUP_OPEN:
            depth += 1
        elif ch in {"}", ">", "]"} and depth:
            depth -= 1
        elif ch == "?" and depth == 0:
            return path[:i]
    return path


def path_from_url(url: str) -> str:
    """Extract the path component of a URL or of an already-bare path."""
    if "://" in url:
        # urlsplit is only safe once a scheme is present: a bare "//a/b" route would
        # otherwise be read as a scheme-relative URL and lose its first segment.
        return _strip_query(urlsplit(url).path) or "/"
    return _strip_query(url.split("#", 1)[0])


def _normalize_segment(seg: str) -> str:
    """Fold one path segment to a literal, PLACEHOLDER or GREEDY."""
    if seg == "*":
        # A bare star is written by hand in catalogs to mean "one variable segment"
        # (the spec pins /api/orders/* == /api/orders/:id), not a catch-all.
        return PLACEHOLDER
    if seg == "**":
        return GREEDY
    if seg.startswith("*"):
        # Rails "*splat", Express 5 "*name": greedy by definition.
        return GREEDY
    if seg.startswith(":"):
        # ":id", ":id?" -- optionality does not change the shape of the segment.
        return PLACEHOLDER
    if seg.startswith("{") and seg.endswith("}"):
        inner = seg[1:-1]
        if inner.startswith("*"):
            return GREEDY  # ASP.NET catch-all {*rest} / {**rest}
        if inner.endswith("..."):
            return GREEDY  # Go 1.22 ServeMux {rest...}
        return PLACEHOLDER  # {id}, {id:int}, {id?}, {id:[0-9]+}
    if seg.startswith("<") and seg.endswith(">"):
        inner = seg[1:-1]
        converter = inner.split(":", 1)[0] if ":" in inner else ""
        return GREEDY if converter == "path" else PLACEHOLDER
    if seg.startswith("[") and seg.endswith("]"):
        inner = seg.strip("[]")
        return GREEDY if inner.startswith("...") else PLACEHOLDER  # Next.js
    if "{" in seg or "<" in seg:
        seg = _EMBEDDED_BRACE.sub(PLACEHOLDER, seg)
        seg = _EMBEDDED_ANGLE.sub(PLACEHOLDER, seg)
    return seg


def route_segments(route: str) -> tuple[str, ...]:
    """Canonical segment tuple for a route template or concrete path."""
    route = (route or "").strip()
    if route == UNMATCHED:
        return (UNMATCHED,)
    route = path_from_url(route)
    route = route.casefold()
    segs = [s for s in route.split("/") if s != ""]
    return tuple(_normalize_segment(s) for s in segs)


def normalize_route(route: str) -> str:
    """Canonical string form of a route, e.g. ``/api/orders/{}``."""
    segs = route_segments(route)
    if not segs:
        return "/"
    if segs == (UNMATCHED,):
        return UNMATCHED
    return "/" + "/".join(segs)


def _match(a: tuple[str, ...], b: tuple[str, ...], *, lenient: bool) -> bool:
    """Segment-wise match, handling greedy placeholders on either side."""
    if not a and not b:
        return True
    if not a or not b:
        return False
    x, y = a[0], b[0]
    if x == GREEDY or y == GREEDY:
        # Normalise so the greedy side is `a`; a greedy placeholder eats 1..n
        # segments of the other side (0 is excluded: a route with a catch-all is
        # not the same route as the one without it).
        if x != GREEDY:
            a, b = b, a
        rest = a[1:]
        for k in range(1, len(b) + 1):
            if _match(rest, b[k:], lenient=lenient):
                return True
        return False
    if x == y:
        return _match(a[1:], b[1:], lenient=lenient)
    if lenient and (x == PLACEHOLDER or y == PLACEHOLDER):
        return _match(a[1:], b[1:], lenient=lenient)
    return False


def routes_equal(a: str, b: str) -> bool:
    """True when two route *templates* denote the same endpoint (strict)."""
    sa, sb = route_segments(a), route_segments(b)
    if sa == (UNMATCHED,) or sb == (UNMATCHED,):
        # An unmatched request proves nothing about any registered route.
        return sa == sb
    return _match(sa, sb, lenient=False)


def route_matches_path(template: str, path: str) -> bool:
    """True when a concrete path is an instance of a route template (lenient)."""
    st, sp = route_segments(template), route_segments(path)
    if st == (UNMATCHED,) or sp == (UNMATCHED,):
        return False
    return _match(st, sp, lenient=True)
