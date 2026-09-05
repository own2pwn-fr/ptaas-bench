"""Sweep everything that ships into the image for words that would give the game away.

The deployed service is read from the inside sooner or later, so the vocabulary in its
source, its templates, its compiled front end, its start-up scripts and its dependency
list all have to read as an ordinary product. The forbidden list is parsed out of the
deployment contract so that a word added there starts failing here rather than the two
drifting apart.

Matching is on whole tokens and on stems, never on substrings: "available",
"collaborator" and "elaborate" all contain "lab", and a sweep that cried wolf on those
would be switched off inside a week. Text is split on punctuation and on case
boundaries first, so ``BenchClient``, ``bench_client`` and ``bench-client`` all reduce
to the same token.

Two things are swept differently and on purpose:

* the vendored framework build is third-party minified code that every site on the web
  also serves; a word inside it says nothing about this deployment, so only the
  unmistakable terms are looked for there;
* the test tree itself is excluded -- it never ships, and it is the one place our own
  vocabulary is allowed to live.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
CONTRACT = HERE.parents[1] / "targets" / "target-contract.yaml"

STEMS = (
    "bench", "vuln", "exploit", "trigger", "oracle", "canary", "honeypot", "testbed",
    "sandbox", "scanner", "scoring", "scorer", "evaluation", "challenge", "insecure",
    "deliberate", "attacker", "adversar", "malicious", "planted", "grader", "pentest",
    "dvwa", "pwn",
)
WORDS = frozenset({"lab", "labs", "ctf", "cve", "poc", "flag", "flags", "flagged",
                   "flagging"})
PHRASES = (
    ("ground", "truth"), ("under", "test"), ("penetration", "test"),
    ("juice", "shop"), ("red", "team"), ("capture", "the", "flag"),
)
CONTRACT_SKIP = {"test", "targets", "flag", "lab", "ctf", "vulnerable", "bench"}

# Third-party builds we serve unchanged. Only the terms that could not be in one by
# accident are looked for.
VENDORED = ("vue.global.prod-3.5.13.js",)
VENDOR_TERMS = ("bench", "ptaas", "dvwa", "honeypot", "testbed", "pentest")


def _tokenise(text: str) -> list[str]:
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    return [token for token in re.split(r"[^A-Za-z0-9]+", spaced.lower()) if token]


def _contract_terms() -> tuple[set[str], set[tuple[str, ...]]]:
    words: set[str] = set()
    phrases: set[tuple[str, ...]] = set()
    if not CONTRACT.exists():
        return words, phrases
    lines = CONTRACT.read_text(encoding="utf-8").splitlines()
    try:
        start = next(i for i, line in enumerate(lines)
                     if line.strip() == "forbidden_strings:")
    except StopIteration:
        return words, phrases
    for line in lines[start + 1:]:
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if not stripped.startswith("- "):
            break
        for entry in stripped[2:].split(","):
            tokens = _tokenise(entry)
            if not tokens:
                continue
            if len(tokens) > 1:
                phrases.add(tuple(tokens))
            elif len(tokens[0]) >= 3 and tokens[0] not in CONTRACT_SKIP:
                words.add(tokens[0])
    return words, phrases


CONTRACT_WORDS, CONTRACT_PHRASES = _contract_terms()
ALL_WORDS = WORDS | CONTRACT_WORDS
ALL_PHRASES = tuple(PHRASES) + tuple(CONTRACT_PHRASES)


def hits(text: str) -> list[str]:
    tokens = _tokenise(text)
    found: list[str] = []
    for token in tokens:
        if token in ALL_WORDS:
            found.append(token)
            continue
        for stem in STEMS:
            if token.startswith(stem):
                found.append(stem)
                break
    for phrase in ALL_PHRASES:
        width = len(phrase)
        if any(tuple(tokens[i:i + width]) == phrase
               for i in range(len(tokens) - width + 1)):
            found.append(" ".join(phrase))
    return sorted(set(found))


def vendor_hits(text: str) -> list[str]:
    tokens = set(_tokenise(text))
    return sorted(term for term in VENDOR_TERMS
                  if any(token.startswith(term) for token in tokens))


def shipped() -> list[Path]:
    """Exactly what the image build copies in, per the Dockerfile and .dockerignore."""
    files: list[Path] = []
    files += [p for p in sorted((HERE / "pressroom").rglob("*"))
              if p.is_file() and "__pycache__" not in p.parts]
    files += [p for p in sorted((HERE / "web").rglob("*")) if p.is_file()]
    files += [HERE / "Dockerfile", HERE / "requirements.txt",
              HERE / "state-reset", HERE / "serve", HERE / ".dockerignore"]
    return [p for p in files if p.exists()]


def test_the_sweep_catches_what_it_is_for():
    for leak in ("this is a benchmark target", "lists the vulnerabilities",
                 "the sink triggered once", "we captured the flags",
                 "stored as ground truth", "class BenchClient:", "BENCH_COLLECTOR_URL",
                 "a lab environment", "raised by the oracle",
                 "what the scanner reported"):
        assert hits(leak), f"{leak!r} should have been caught"


def test_the_sweep_does_not_cry_wolf():
    for word in ("available", "collaborator", "elaborate", "labelled", "labour",
                 "flagship", "contest", "latest", "protest", "score", "harbour"):
        assert hits(word) == [], f"{word} must not be a hit"
    assert hits("the picture desk imports wire photography by address") == []


@pytest.mark.parametrize("path", shipped(), ids=lambda p: p.name)
def test_shipped_files_carry_no_tell(path: Path):
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return  # a picture or an icon: nothing to read out of it either
    if path.name in VENDORED:
        assert vendor_hits(text) == [], f"{path} leaks {vendor_hits(text)}"
        return
    assert hits(text) == [], f"{path} leaks {hits(text)}"


def test_shipped_filenames_carry_no_tell():
    for path in shipped():
        assert hits(path.name) == [], f"{path.name} is itself a tell"
    for path in shipped():
        relative = path.relative_to(HERE)
        assert hits(str(relative.parent)) == [], f"{relative} sits in a telling path"


def test_every_environment_variable_the_service_reads_is_product_shaped():
    """A stray name would show up in `env` on a host somebody has taken."""
    names: set[str] = set()
    for path in (HERE / "pressroom").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        names |= set(re.findall(
            r"os\.environ(?:\.get)?[\(\[]\s*[\"']([A-Z0-9_]+)[\"']", source))
    assert names
    allowed_prefixes = ("SITE_", "DEPLOY_", "MONGO_", "REDIS_", "MEDIA_", "OPS_",
                        "PLUGIN_", "SESSION_", "PREVIEW_", "ANALYTICS_", "ASSIST_",
                        "RECOVERY_", "USER")
    for name in names:
        assert name.startswith(allowed_prefixes), name


def test_the_signal_names_are_metric_shaped_and_carry_no_identifier():
    """A count a compromised host can read must look like a counter, not a verdict."""
    shape = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
    found: set[str] = set()
    for path in (HERE / "pressroom").rglob("*.py"):
        found |= set(re.findall(r'"(blog\.[a-z0-9_.]+)"',
                                path.read_text(encoding="utf-8")))
    assert len(found) >= 20, sorted(found)
    for name in found:
        assert shape.match(name), name
        assert hits(name) == [], name
        assert "-" not in name and name.islower()


def test_the_deployment_scripts_do_not_name_a_reset_route():
    """Provisioning is a command. Anything reachable over HTTP is reachable by anyone."""
    source = (HERE / "pressroom").rglob("*.py")
    for path in source:
        text = path.read_text(encoding="utf-8")
        assert "state-reset" not in text, path


def test_what_the_service_actually_serves_carries_no_tell(stack, monkeypatch):
    """Sweep live responses, not just the source: the spec and the shell are generated.

    The diagnostics view reports the working directory, which in the image is
    /srv/pressroom and here is wherever this repository happens to sit. The deployed
    value is the one that has to be swept, so it is the one the sweep is given -- the
    checkout path is not part of what anybody deploys.
    """
    monkeypatch.setattr("os.getcwd", lambda: "/srv/pressroom")
    slug = stack.client.get("/api/articles").json()["items"][0]["slug"]
    paths = ["/", "/openapi.json", "/robots.txt", "/humans.txt", "/feed.xml",
             "/feed.json", "/sitemap.xml", "/sitemap-pages.xml", "/sitemap-articles.xml",
             "/.well-known/security.txt", "/opensearch.xml", "/site.webmanifest",
             "/api/status", "/api/config", "/api/articles", "/api/topics", "/api/authors",
             "/api/comments", "/api/newsletter/issues", "/api/embed/providers",
             f"/api/articles/{slug}", f"/api/articles/{slug}/comments",
             "/api/internal/diagnostics", "/api/search?q=harbour"]
    for path in paths:
        response = stack.client.get(path)
        assert response.status_code == 200, (path, response.status_code)
        found = hits(response.text)
        assert found == [], f"{path} leaks {found}"


def test_refusals_carry_no_tell(stack):
    """Error bodies are read more often than success bodies."""
    for method, path, body in (
        ("GET", "/api/articles/not-a-real-slug", None),
        ("GET", "/api/articles/archive?match=nonsense", None),
        ("GET", "/api/authors?filter=notjson", None),
        ("GET", "/api/studio/drafts", None),
        ("GET", "/api/nothing/here", None),
        ("POST", "/api/auth/session", {}),
        ("POST", "/api/articles/x/comments", {"body": ""}),
    ):
        response = stack.client.request(method, path, json=body)
        assert response.status_code >= 400, path
        found = hits(response.text)
        assert found == [], f"{method} {path} leaks {found}"
