"""The normalised finding record, and the vocabularies it is allowed to use.

This module is the contract between the drivers and the scoring engine. The scorer
matches a tool's *claim* against the catalog's *ground truth*, so the only fields
that exist here are the ones a claim can be judged on:

    tool, url, method, param, cwe, name, severity, confidence, raw_ref

Two deliberate omissions:

* No "app" field. Which target a claim belongs to is derived from the URL by the
  scorer, which already owns the target registry. Duplicating it here would let a
  driver bug silently re-attribute findings between applications.
* No free-form evidence blob. ``raw_ref`` points back into the tool's own raw output
  (kept verbatim under results/runs/<run_id>/raw/), which is what a third party
  auditing a published number will want to read anyway.

``cwe`` is ``None`` whenever the mapping table cannot justify a value. That is not
laziness: the scorer compares the claimed CWE with the planted one, so a *guessed*
CWE turns a true positive into a false positive (and, worse, occasionally the other
way round). Unknown is a legitimate, honest answer and is scored as such.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# The catalog uses this severity vocabulary (catalog/schema.json), so normalised
# findings use it too: any other scale would have to be translated at scoring time,
# i.e. in the component that must stay free of tool-specific knowledge.
SEVERITIES = ("info", "low", "medium", "high", "critical")
CONFIDENCES = ("low", "medium", "high", "confirmed")

# HTTP methods we accept verbatim. Anything else is normalised to None rather than
# passed through, because the scorer keys reach/exercise on (method, route).
METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "TRACE")


def norm_severity(value: Any, *, default: str | None = None) -> str | None:
    """Map a tool's severity word onto the catalog vocabulary.

    Unknown words return ``default`` (usually None) instead of being coerced to
    "medium": an invented severity would quietly distort the severity-weighted
    comparison tables.
    """
    if value is None:
        return default
    text = str(value).strip().lower()
    aliases = {
        "informational": "info",
        "information": "info",
        "info": "info",
        "note": "info",
        "none": "info",
        "unknown": default,
        "low": "low",
        "medium": "medium",
        "moderate": "medium",
        "warning": "medium",
        "high": "high",
        "important": "high",
        "critical": "critical",
        "severe": "critical",
        "emergency": "critical",
    }
    return aliases.get(text, default)


def norm_confidence(value: Any, *, default: str | None = None) -> str | None:
    """Map a tool's confidence word onto ``CONFIDENCES``.

    "confirmed" is kept distinct from "high" on purpose: ZAP and Arachni both have a
    tier that means "the scanner proved it", and the report separates claimed-and-
    proved from claimed-only.
    """
    if value is None:
        return default
    text = str(value).strip().lower()
    aliases = {
        "false positive": "low",
        "falsepositive": "low",
        "tentative": "low",
        "low": "low",
        "medium": "medium",
        "moderate": "medium",
        "firm": "high",
        "high": "high",
        "certain": "confirmed",
        "confirmed": "confirmed",
        "trusted": "confirmed",
    }
    return aliases.get(text, default)


def norm_method(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().upper()
    return text if text in METHODS else None


@dataclass(slots=True)
class Finding:
    """One claim made by one tool.

    ``raw_ref`` is a stable pointer of the form ``<relative raw file>#<locator>``,
    e.g. ``raw/zap.json#site[0].alerts[3].instances[1]`` or ``raw/nuclei.jsonl#12``.
    Anyone disputing a scored result must be able to find the tool's own words.
    """

    tool: str
    url: str | None = None
    method: str | None = None
    param: str | None = None
    cwe: int | None = None
    name: str | None = None
    severity: str | None = None
    confidence: str | None = None
    raw_ref: str | None = None

    def __post_init__(self) -> None:
        if self.severity is not None and self.severity not in SEVERITIES:
            raise ValueError(f"severity {self.severity!r} outside {SEVERITIES}")
        if self.confidence is not None and self.confidence not in CONFIDENCES:
            raise ValueError(f"confidence {self.confidence!r} outside {CONFIDENCES}")
        if self.cwe is not None and (not isinstance(self.cwe, int) or self.cwe <= 0):
            raise ValueError(f"cwe must be a positive int or None, got {self.cwe!r}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class NormaliseResult:
    """Findings plus the audit trail of what could not be mapped.

    ``unmapped`` is written next to the findings so the operator sees, per run,
    exactly which tool outputs the CWE table does not know about. That list is the
    to-do list for the mapping table; silently emitting null forever would let the
    table rot without anyone noticing.
    """

    findings: list[Finding] = field(default_factory=list)
    unmapped: list[dict[str, Any]] = field(default_factory=list)

    def extend(self, other: NormaliseResult) -> None:
        self.findings.extend(other.findings)
        self.unmapped.extend(other.unmapped)

    def write(self, findings_path: Path, unmapped_path: Path | None = None) -> None:
        findings_path.parent.mkdir(parents=True, exist_ok=True)
        findings_path.write_text(
            json.dumps([f.to_dict() for f in self.findings], indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        if unmapped_path is not None:
            unmapped_path.write_text(
                json.dumps(self.unmapped, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
