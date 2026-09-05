"""Canary token extraction -- the crux of the whole service.

A callback is only evidence if we can say *which* vulnerability it proves, so every
hit is mined for a token. Two forms are supported:

* static  ``shop0031``            -- the value a catalog entry declares in
  ``oracle.canary_token``; every hit on it maps to that vulnerability.
* dynamic ``shop0031-9f2c``       -- ``<token>-<nonce>``; the base token still maps to
  the vulnerability, the nonce distinguishes repeated hits (a tool re-firing the same
  payload, or the platform's own seeding) without needing a second token.

A token is ``[a-z0-9]{4,32}``. The dash is not in that alphabet, which is what makes
the dynamic form unambiguous: the *first* dash always separates token from nonce, so
the nonce itself may contain dashes and nothing gets mis-split.

Extraction order is fixed and global (``SOURCE_RANK``), not per-channel, so that a
callback carrying the token in two places always resolves the same way:

    1. leftmost DNS label      ``shop0031.oob.bench.local``
    2. HTTP Host header        ``Host: shop0031.oob.bench.local``
    3. first path segment      ``GET /shop0031/x``
    4. HTTP query ``t=``       ``GET /x?t=shop0031``
    5. SMTP envelope localpart ``RCPT TO:<shop0031@oob.bench.local>``
    6. LDAP DN                 ``cn=shop0031,dc=oob,dc=bench,dc=local``

The domain part of an SMTP *recipient* is ranked with rule 1 when it is inside our
zone: it is a DNS name, and ``x@shop0031.oob.bench.local`` hides the token in exactly
the same place as a DNS query would. Everything else about an envelope ranks below the
six rules, because it is not attacker-controlled in the way the recipient is -- the
sender is usually the target application's own domain, and letting ``app@target.invalid``
outrank ``RCPT TO:<shop0031@...>`` would attribute the callback to the wrong thing (it
did, in an earlier revision; see tests/test_smtp.py).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Sequence

TOKEN_RE = re.compile(r"^[a-z0-9]{4,32}$")
# The nonce is allowed to be richer than the token (dashes, up to 64 chars) because it
# is opaque to us: only the base token is correlated against the catalog.
DYNAMIC_RE = re.compile(r"^([a-z0-9]{4,32})-([a-z0-9][a-z0-9-]{0,63})$")

# Marker used in the collector event when nothing token-shaped was found. The OobEvent
# schema makes `token` required, so we cannot send null; the store keeps None.
UNKNOWN_TOKEN = "unknown"

SOURCE_RANK: dict[str, int] = {
    "dns_label": 10,
    "host_header": 20,
    "path_segment": 30,
    "query_t": 40,
    "smtp_localpart": 50,
    "ldap_dn": 60,
    # Below the six rules: envelope parts that the payload does not usually control.
    "smtp_domain": 70,   # recipient domain outside our zone
    "smtp_sender": 75,   # anything from MAIL FROM
}


@dataclass(frozen=True)
class Candidate:
    """One place a token might be hiding, with the rule that found it."""

    source: str
    value: str

    @property
    def rank(self) -> int:
        return SOURCE_RANK.get(self.source, 99)


@dataclass(frozen=True)
class Extraction:
    """Result of mining a callback.

    ``token`` is None when nothing matched the token grammar; ``candidate`` then holds
    the best-ranked non-empty string we saw, so an unknown callback is still reported
    with something a human can recognise (a tool's own collaborator subdomain, say).
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
        """Token as it appeared on the wire, i.e. with the nonce when there was one."""
        if self.token is None:
            return self.candidate or ""
        return f"{self.token}-{self.nonce}" if self.nonce else self.token


def parse_token(value: str | None) -> tuple[str, str | None] | None:
    """Parse a candidate string into ``(token, nonce)``, or None if it is not one."""
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
    """Pick the token from an unordered bag of candidates, by rule priority."""
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
    """Leftmost label of a hostname, with the service's own zone stripped first.

    ``shop0031.oob.bench.local`` -> ``shop0031``; the bare zone -> None (no token);
    a foreign name (``x7d9k2.collab.example``) still yields its leftmost label, which
    is how we notice a tool testing with its own collaborator domain.
    """
    if not host:
        return None
    name = host.strip().strip(".").lower()
    if not name:
        return None
    # Strip an IPv6 literal's brackets / a port suffix if one leaked in from a Host header.
    if name.startswith("["):
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
    """Value of the ``t=`` parameter, hand-parsed to stay lenient about junk queries."""
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

    Covers what JNDI actually puts on the wire: ``cn=shop0031,dc=example`` from a
    search base, but also the bare object name a URL like ``ldap://host:389/shop0031``
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
