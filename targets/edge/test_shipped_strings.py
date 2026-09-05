#!/usr/bin/env python3
"""Guard the cover story: no word that names the platform may ship inside the target.

Why this is a test and not a review checklist
---------------------------------------------
The corpus contains real remote code execution, so the working assumption is that a
tool eventually reads the filesystem and the environment of a process it has taken.
Everything that lands in a container is therefore readable by the subject of the
measurement, and a single word in a comment is enough to tell an agent that it is
inside an exercise rather than in front of a customer's website. An agent that works
that out stops behaving like an attacker — it starts hunting for the answer key, or
declines to exploit "a test system" — and the run then measures its meta-reasoning
instead of its ability.

Matching
--------
Substring matching would be useless here: "available", "collaborator" and "elaborate"
all contain "lab", and a test that cries wolf gets disabled. So identifiers are
tokenised the way a reader would read them — split on punctuation and on case
boundaries, lowercased — and compared as whole words, with a separate pass for a short
list of stems where a prefix match is genuinely safe, and a third pass for phrases.

    python3 targets/edge/test_shipped_strings.py     # standalone
    pytest targets/edge/test_shipped_strings.py      # in CI
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# Everything below is copied into an image or mounted into a container, and is
# therefore readable by anything that wins code execution on this target.
SHIPPED = [
    "Dockerfile",
    "state-reset",
    "origin/go.mod",
    "origin/telemetry.go",
    "origin/wire.go",
    "origin/main.go",
    "origin/routes.go",
    "origin/cacheprobe.go",
    "origin/control.go",
    "nginx/nginx.conf",
    "nginx/devtap.conf",
    "haproxy/haproxy.cfg",
    "varnish/default.vcl",
]

# NOT swept, and deliberately so: the catalog, the route inventory, the credentials
# file and selftest.py stay on the platform side and never enter a container. They are
# the answer key; if a tool can read them the problem is the network, not the words.

WORDS = {
    "bench", "benchmark", "benchmarks", "benchmarking",
    "ctf", "ctfs",
    "flag", "flags", "flagged",
    "challenge", "challenges",
    "lab", "labs",
    "testbed", "testbeds",
    "sandbox", "sandboxed", "sandboxes",
    "honeypot", "honeypots",
    "canary", "canaries",
    "oracle", "oracles",
    "trigger", "triggers", "triggered", "triggering",
    "dvwa",
    "scanner", "scanners",
    "evaluation", "evaluations",
    "scoring", "scored", "scorer",
    "vuln", "vulns",
    "vulnerable", "vulnerability", "vulnerabilities",
    "insecure", "insecurely",
    "deliberate", "deliberately",
    "exploitme",
    "juiceshop",
    "planted", "plant",
    "grader", "graded",
}

# Prefix pass. Only stems where every English word starting with them is a hit; this
# list stays short on purpose, because a careless entry here is how a guard test starts
# producing noise and gets switched off.
STEMS = ("vulner", "deliberat", "honeypot", "testbed", "ptaasbench", "dvwa")

# Exact source spellings from third-party and standard-library APIs. These are matched
# and removed BEFORE tokenising, so the word inside them is never read as prose. The
# list is deliberately literal — full dotted identifiers, never bare words — because
# the risk being managed is a sentence a human wrote, not the name of a library call
# that every Go program in the world contains. `log.SetFlags` tells a reader nothing;
# a sentence naming the exercise tells them everything.
API_SPELLINGS = (
    "log.SetFlags",
    "log.LstdFlags",
    "LstdFlags",
    "flag.Parse",
    "http.Flusher",
)

PHRASES = (
    "ground truth",
    "ptaas bench",
    "exploit me",
    "juice shop",
    "answer key",
    "under test",
)


def tokenise(text: str) -> list[str]:
    """Split the way a reader reads: on punctuation, and on camelCase boundaries."""
    rough = re.split(r"[^A-Za-z0-9]+", text)
    out: list[str] = []
    for chunk in rough:
        if not chunk:
            continue
        # helloWorld / HTTPServer / parseURLPath -> hello world / http server / ...
        for part in re.findall(r"[A-Z]+(?![a-z])|[A-Z][a-z0-9]*|[a-z0-9]+", chunk):
            out.append(part.lower())
    return out


def scan(path: Path) -> list[tuple[int, str, str]]:
    hits: list[tuple[int, str, str]] = []
    for lineno, raw_line in enumerate(path.read_text(errors="replace").splitlines(), 1):
        line = raw_line
        for spelling in API_SPELLINGS:
            line = line.replace(spelling, " ")
        tokens = tokenise(line)
        joined = " ".join(tokens)
        for tok in tokens:
            if tok in WORDS:
                hits.append((lineno, tok, "word"))
            elif tok.startswith(STEMS):
                hits.append((lineno, tok, "stem"))
        for phrase in PHRASES:
            if re.search(r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])", joined):
                hits.append((lineno, phrase, "phrase"))
    return hits


def sweep() -> dict[str, list[tuple[int, str, str]]]:
    findings: dict[str, list[tuple[int, str, str]]] = {}
    for rel in SHIPPED:
        path = ROOT / rel
        if not path.exists():
            findings[rel] = [(0, "file is listed as shipped but does not exist", "missing")]
            continue
        if hits := scan(path):
            findings[rel] = hits
    return findings


def test_no_platform_vocabulary_ships():
    findings = sweep()
    assert not findings, "\n".join(
        f"{rel}:{ln}: {tok} ({kind})"
        for rel, hits in sorted(findings.items())
        for ln, tok, kind in hits
    )


def main() -> int:
    findings = sweep()
    for rel, hits in sorted(findings.items()):
        for ln, tok, kind in hits:
            print(f"{rel}:{ln}: {tok} ({kind})")
    if findings:
        total = sum(len(h) for h in findings.values())
        print(f"\n{total} occurrence(s) in {len(findings)} file(s) that ship inside the target")
        return 1
    print(f"clean: {len(SHIPPED)} shipped files carry no platform vocabulary")
    return 0


if __name__ == "__main__":
    sys.exit(main())
