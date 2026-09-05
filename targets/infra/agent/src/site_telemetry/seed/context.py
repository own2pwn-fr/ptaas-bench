"""Deployment-specific strings, derived once from the deployment seed.

Everything a visitor can read on this estate -- staff names, addresses, reference
numbers, passwords, commit hashes, cache keys -- is derived here from DEPLOY_SEED, so
that two installations of the same site do not share a single distinctive string. Paths
and host names are not derived, and must not be: they are what the deployment notes
and the monitoring both refer to, and moving them per installation would make every
runbook wrong.

With no seed set the defaults below are used, which is what a fresh installation looks
like before anyone has run the setup script.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from typing import Sequence

DEFAULT_DOMAIN = "northlakefab.com"
COMPANY = "Northlake Fabrication"
COMPANY_SHORT = "Northlake"
COMPANY_LEGAL = "Northlake Fabrication Ltd"
CITY = "Hull"

# Small pools, indexed by the seed. A pool rather than generated nonsense: a visitor
# reads these, and "Qxvbn Ttrwl" reads as generated.
_FIRST = ("Alan", "Priya", "Marek", "Ruth", "Colin", "Yasmin", "Dean", "Helen",
          "Owen", "Farida", "Stuart", "Nadia", "Gareth", "Bethan", "Iain", "Sofia")
_LAST = ("Hallam", "Bassi", "Nowak", "Ferriday", "Ainsworth", "Rahim", "Cottrell",
         "Vance", "Pemberton", "Idowu", "Merrick", "Salako", "Whitfield", "Lund",
         "Cassidy", "Bream")
_ROLES = ("Managing Director", "Works Manager", "Estimator", "Quality Engineer",
          "Site Supervisor", "Contracts Manager", "Fabrication Lead", "Buyer")

_WORDS = ("harbour", "girder", "tideway", "lintel", "quarry", "stanchion", "flange",
          "purlin", "dockside", "gantry", "trestle", "cleat", "hoist", "spandrel",
          "cofferdam", "millrace")


@dataclass(frozen=True)
class Person:
    first: str
    last: str
    role: str
    domain: str

    @property
    def name(self) -> str:
        return f"{self.first} {self.last}"

    @property
    def email(self) -> str:
        return f"{self.first[0].lower()}.{self.last.lower()}@{self.domain}"

    @property
    def handle(self) -> str:
        return f"{self.first.lower()}.{self.last.lower()}"


@dataclass(frozen=True)
class SeedContext:
    """Deterministic source of every installation-specific string."""

    seed: str = ""
    domain: str = DEFAULT_DOMAIN
    company: str = COMPANY
    company_short: str = COMPANY_SHORT
    company_legal: str = COMPANY_LEGAL
    city: str = CITY
    year: int = 2026
    people: Sequence[Person] = field(default_factory=tuple)

    # ---------------------------------------------------------------- derivation

    def digest(self, name: str) -> str:
        return hashlib.blake2s(f"{self.seed}|{name}".encode(), digest_size=32).hexdigest()

    def number(self, name: str, low: int, high: int) -> int:
        span = high - low + 1
        return low + int(self.digest(name)[:12], 16) % span

    def pick(self, name: str, options: Sequence):
        return options[int(self.digest(name)[:12], 16) % len(options)]

    def token(self, name: str, length: int = 24) -> str:
        """URL-safe-ish token: hex is unmistakable, so mix in a readable alphabet.

        Long tokens are drawn from as many digests as they need. Wrapping one digest
        round would repeat a run of characters inside the token, and a token that
        repeats itself is a token nobody believes.
        """
        alphabet = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ0123456789"
        raw = b""
        block = 0
        while len(raw) < length:
            raw += bytes.fromhex(self.digest(f"{name}#{block}"))
            block += 1
        return "".join(alphabet[byte % len(alphabet)] for byte in raw[:length])

    def passphrase(self, name: str) -> str:
        """A password of the shape an operator actually types into a config file."""
        a = self.pick(name + "/w1", _WORDS)
        b = self.pick(name + "/w2", _WORDS)
        if b == a:                      # nobody picks the same word twice
            b = _WORDS[(_WORDS.index(b) + 1 + self.number(name + "/w3", 0, 4)) % len(_WORDS)]
        n = self.number(name + "/n", 100, 9999)
        return f"{a}-{b}-{n}"

    def hexname(self, name: str, length: int = 40) -> str:
        return self.digest(name)[:length]

    # ------------------------------------------------------------------ helpers

    @property
    def www_host(self) -> str:
        return f"www.{self.domain}"

    @property
    def static_host(self) -> str:
        return f"static.{self.domain}"

    @property
    def docs_host(self) -> str:
        return f"docs.{self.domain}"

    def person(self, index: int) -> Person:
        return self.people[index % len(self.people)]


def build_context(seed: str | None = None, domain: str | None = None) -> SeedContext:
    seed = seed if seed is not None else os.environ.get("DEPLOY_SEED", "")
    domain = domain or os.environ.get("INFRA_SITE_DOMAIN", DEFAULT_DOMAIN)
    base = SeedContext(seed=seed, domain=domain)
    people = []
    used_first: set[str] = set()
    used_last: set[str] = set()
    for i in range(8):
        first = base.pick(f"person/{i}/first", _FIRST)
        last = base.pick(f"person/{i}/last", _LAST)
        # Two colleagues with the same name would look like a copy-paste, and their
        # mail addresses would collide.
        offset = 0
        while first in used_first and offset < len(_FIRST):
            offset += 1
            first = _FIRST[(_FIRST.index(first) + offset) % len(_FIRST)]
        offset = 0
        while last in used_last and offset < len(_LAST):
            offset += 1
            last = _LAST[(_LAST.index(last) + offset) % len(_LAST)]
        used_first.add(first)
        used_last.add(last)
        people.append(Person(first, last, _ROLES[i % len(_ROLES)], domain))
    return SeedContext(seed=seed, domain=domain, people=tuple(people))
