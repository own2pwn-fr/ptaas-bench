"""Extracting an identifier out of whatever a payload aimed at us.

Most requests that land here name a host chosen by the tool under test, not by us, so
the identifier is usually the tool's own: ``z9x2k1p8.example-collab.net`` carries
``z9x2k1p8``. Grouping by it is still worth a lot -- it ties a DNS lookup, the HTTP
connection that follows it and a later SMTP hit together as one attempt -- even though
the mapping back to a planted defect comes from correlation.py rather than from here.

Two written forms are also understood, for hosts we do own:

* static  ``shop0031``          -- a fixed label a catalog entry declares, for cases
  where a payload template is written against our own zone;
* dynamic ``shop0031-9f2c``     -- ``<label>-<nonce>``; the base label still maps to
  the catalog entry, the nonce keeps repeated hits distinguishable.

Labels are ``[a-z0-9]{4,32}``. The dash is outside that alphabet, which makes the
dynamic form unambiguous: the *first* dash always separates label from nonce, so the
nonce itself may contain dashes and nothing gets mis-split.

Extraction order is fixed and global (``SOURCE_RANK``), not per-channel, so a request
carrying an identifier in two places always resolves the same way:

    1. leftmost DNS label      ``shop0031.telemetry-edge.net``
    2. HTTP Host header        ``Host: shop0031.telemetry-edge.net``
    3. first path segment      ``GET /shop0031/x``
    4. HTTP query ``t=``       ``GET /x?t=shop0031``
    5. SMTP envelope localpart ``RCPT TO:<shop0031@...>``
    6. LDAP DN                 ``cn=shop0031,dc=...``

Below those six sit two more, and the reason is a real mis-attribution we hit: an
address domain is a DNS name, so ``noreply@shop0031.<zone>`` is mined with rule 1 --
but only for the *recipient*. Ranking a MAIL FROM domain that way made
``app@retail.internal`` outrank ``RCPT TO:<shop0031@...>`` and credit the wrong thing.
Sender parts and out-of-zone recipient domains therefore rank last.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Sequence

TOKEN_RE = re.compile(r"^[a-z0-9]{4,32}$")
# The nonce may be richer than the label (dashes, up to 64 chars) because it is opaque
# to us: only the base label is ever matched against the catalog.
DYNAMIC_RE = re.compile(r"^([a-z0-9]{4,32})-([a-z0-9][a-z0-9-]{0,63})$")

# Used in the reported event when nothing identifier-shaped was found. The event schema
# makes the field required, so we cannot send null there; the local store keeps None.
UNIDENTIFIED = "unidentified"

SOURCE_RANK: dict[str, int] = {
    "dns_label": 10,
    "host_header": 20,
    "path_segment": 30,
    "query_t": 40,
    "smtp_localpart": 50,
    "ldap_dn": 60,
    # Below the six rules: envelope parts a payload does not usually control.
    "smtp_domain": 70,   # recipient domain outside our own zone
    "smtp_sender": 75,   # anything taken from MAIL FROM
}


@dataclass(frozen=True)
class Candidate:
    """One place an identifier might be hiding, with the rule that found it."""

    source: str
    value: str

    @property
    def rank(self) -> int:
        return SOURCE_RANK.get(self.source, 99)


@dataclass(frozen=True)
class Extraction:
    """Result of mining one request.

    ``token`` is None when nothing matched the grammar; ``candidate`` then holds the
    best-ranked non-empty string we saw, so the request is still recognisable.
    """

    token: str | None = None
    nonce: str | None = None
    source: str | None = None
    candidate: str | None = None

    @property
    def found(self) -> bool:
        return self.token is not None

    @property
    def label(self) -> str:
        """The identifier as it appeared on the wire, nonce included."""
        if self.token is None:
            return self.candidate or ""
        return f"{self.token}-{self.nonce}" if self.nonce else self.token


def parse_token(value: str | None) -> tuple[str, str | None] | None:
    """Parse a candidate string into ``(label, nonce)``, or None if it is not one."""
    if not value:
        return None
    candidate = value.strip().lower()
    if TOKEN_RE.match(candidate):
        return candidate, None
    dynamic = DYNAMIC_RE.match(candidate)
    if dynamic:
        return dynamic.group(1), dynamic.group(2)
    return None


def extract(candidates: Iterable[Candidate]) -> Extraction:
    """Pick the identifier from an unordered bag of candidates, by rule priority."""
    ordered = sorted(
        (c for c in candidates if c.value), key=lambda c: (c.rank, c.source)
    )
    for candidate in ordered:
        parsed = parse_token(candidate.value)
        if parsed:
            return Extraction(
                token=parsed[0],
                nonce=parsed[1],
                source=candidate.source,
                candidate=candidate.value.strip().lower()[:128],
            )
    if ordered:
        return Extraction(candidate=ordered[0].value.strip().lower()[:128])
    return Extraction()


def host_label(host: str | None, zone: str) -> str | None:
    """Leftmost label of a hostname, with our own zone stripped first.

    ``shop0031.<zone>`` -> ``shop0031``; the bare zone -> None; a foreign name
    (``z9x2k1p8.example-collab.net``) still yields its leftmost label, which is exactly
    the shape a tool's own callback host has.
    """
    if not host:
        return None
    name = host.strip().strip(".").lower()
    if not name:
        return None
    if name.startswith("["):  # IPv6 literal from a Host header
        return None
    if name.count(":") == 1:
        name = name.split(":", 1)[0]
    if name == zone:
        return None
    if zone and name.endswith("." + zone):
        name = name[: -(len(zone) + 1)]
    labels = [label for label in name.split(".") if label]
    if not labels:
        return None
    return labels[0]


def first_path_segment(path: str | None) -> str | None:
    if not path:
        return None
    for segment in path.split("?", 1)[0].split("/"):
        if segment:
            return segment.strip().lower()
    return None


def query_token(query: str | None) -> str | None:
    if not query:
        return None
    from urllib.parse import parse_qs

    values = parse_qs(query, keep_blank_values=False).get("t")
    if not values:
        return None
    return values[0].strip().lower()


def address_parts(address: str | None) -> tuple[str | None, str | None]:
    """Split an SMTP address into ``(localpart, domain)``, tolerating ``<...>`` forms."""
    if not address:
        return None, None
    addr = address.strip()
    if addr.startswith("<") and addr.endswith(">"):
        addr = addr[1:-1].strip()
    if "@" not in addr:
        return (addr.lower() or None), None
    local, _, domain = addr.rpartition("@")
    return (local.strip().lower() or None), (domain.strip().lower() or None)


_DN_SPLIT = re.compile(r"[,/;+]")


def dn_values(dn: str | None) -> list[str]:
    """Attribute values of a DN, most specific first.

    Covers what a JNDI client actually puts on the wire: ``cn=shop0031,dc=example``
    from a search base, and the bare object name a URL like ``ldap://host:389/shop0031``
    turns into.
    """
    if not dn:
        return []
    out: list[str] = []
    for part in _DN_SPLIT.split(dn):
        chunk = part.strip()
        if not chunk:
            continue
        _, sep, value = chunk.partition("=")
        out.append((value if sep else chunk).strip().lower())
    return out


def dn_candidates(dn: str | None) -> Sequence[Candidate]:
    return [Candidate("ldap_dn", value) for value in dn_values(dn)]
