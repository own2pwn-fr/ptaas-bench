"""Shared test scaffolding.

Two rules for this test suite, both non-negotiable:

* **No docker.** Every test that touches the run lifecycle drives a fake client.
  A test that starts a container is a test that will not run in CI, on a laptop
  with a full disk, or next to another agent's benchmark.
* **Fixtures are written from the tools' documented output schemas**, field name by
  field name, including the awkward parts (ZAP's numbers-as-strings, wapiti's empty
  category lists, nikto's 2.6 rename of `banner`, a nuclei JSONL truncated
  mid-line by a budget kill). A fixture that is prettier than reality tests nothing.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

FIXTURES = Path(__file__).resolve().parent / "fixtures"

from runners._lib.normalise import CweTable  # noqa: E402  (needs the sys.path above)


@pytest.fixture(scope="session")
def fixtures() -> Path:
    return FIXTURES


@pytest.fixture(scope="session")
def table() -> CweTable:
    """The real mapping table. Tests assert against what ships, not a stub."""
    return CweTable.load()
