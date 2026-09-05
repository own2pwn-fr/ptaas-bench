"""The mapping table is data, so it gets tested like data.

Four things are checked, and each one corresponds to a way a benchmark quietly
starts lying:

* **Structure.** An entry with no `cwe` key is an omission someone forgot to
  finish; the loader must refuse it rather than default it to null.
* **Justification.** Every entry carries a `source`. An unexplained CWE is an
  opinion, and the whole point of publishing the table is that a reader can
  disagree with a specific line.
* **Vocabulary.** A CWE that is neither in catalog/taxonomy.yaml nor declared in
  `out_of_catalog` can never match ground truth, so it is either a typo or an
  undocumented decision. Both are worth failing a build over.
* **Completeness.** The wapiti and skipfish sections are checked against those
  tools' own full vocabularies (38 report categories, 82 issue codes), because
  those two tools publish no CWE at all: a category missing from the table is a
  finding silently downgraded to "unknown", which reads in the results exactly like
  the tool having missed something.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from runners._lib.normalise import CWE_MAP_PATH, CweTable

REPO_ROOT = Path(__file__).resolve().parents[2]

# wapiti 3.3.2's complete vulnerability-category registry (wapitiCore/definitions/).
# Every one of these can appear as a key in the report's `vulnerabilities` object.
WAPITI_CATEGORIES = {
    "Backup file",
    "Blind SQL Injection",
    "Cleartext Submission of Password",
    "Clickjacking Protection",
    "Command execution",
    "Content Security Policy Configuration",
    "CRLF Injection",
    "Cross Site Request Forgery",
    "CVE-2024-55591",
    "Fingerprint web application framework",
    "Fingerprint web server",
    "HTML Injection",
    "Htaccess Bypass",
    "HTTP Strict Transport Security (HSTS)",
    "HttpOnly Flag cookie",
    "Inconsistent Redirection",
    "Information Disclosure - Full Path",
    "LDAP Injection",
    "Log4Shell",
    "MIME Type Confusion",
    "NS takeover",
    "Open Redirect",
    "Path Traversal",
    "Potentially dangerous file",
    "Reflected Cross Site Scripting",
    "Secure Flag cookie",
    "Server Side Request Forgery",
    "Spring4Shell",
    "SQL Injection",
    "Stack Trace Disclosure",
    "Stored Cross Site Scripting",
    "Stored HTML Injection",
    "Subdomain takeover",
    "TLS/SSL misconfigurations",
    "Unencrypted Channels",
    "Unrestricted File Upload",
    "Vulnerable software",
    "Weak credentials",
    "XML External Entity",
}

# skipfish 2.10b's `var issue_desc` map from assets/index.html: the complete set of
# codes the tool can put in its report. 40303 deliberately does not exist.
SKIPFISH_ISSUE_CODES = {
    10101, 10201, 10202, 10203, 10204, 10205,
    10401, 10402, 10403, 10404, 10405,
    10501, 10502, 10503, 10504, 10505,
    10601, 10602, 10603, 10701,
    10801, 10802, 10803, 10804,
    10901, 10902, 10909,
    20101, 20102, 20201, 20202, 20203, 20204, 20205, 20301,
    30101, 30201, 30202, 30203, 30204, 30205, 30206,
    30301, 30401, 30402, 30501, 30502, 30503, 30601, 30602, 30603,
    30701, 30801, 30901, 30909,
    40101, 40102, 40103, 40104, 40105, 40201, 40202,
    40301, 40302, 40304, 40305, 40401, 40402, 40501, 40601, 40701, 40909,
    50101, 50102, 50103, 50104, 50105, 50106, 50107, 50201, 50301, 50909,
}
# Defined in src/database.h but never emitted by any code path. Listed in the table
# so it is provably complete against the tool's constants, not just its UI map.
SKIPFISH_DEAD_CONSTANTS = {10102, 30802}


@pytest.fixture(scope="module")
def raw() -> dict:
    return yaml.safe_load(CWE_MAP_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def catalog_cwes() -> set[int]:
    """Every CWE any catalog class declares. Ground truth speaks this vocabulary."""
    doc = yaml.safe_load((REPO_ROOT / "catalog" / "taxonomy.yaml").read_text(encoding="utf-8"))
    found: set[int] = set()
    for spec in (doc.get("classes") or {}).values():
        for cwe in spec.get("cwe") or []:
            found.add(int(cwe))
    assert found, "taxonomy.yaml declared no CWEs; the check below would be vacuous"
    return found


def _all_entries(raw: dict):
    for tool, cfg in (raw.get("tools") or {}).items():
        for key, entry in (cfg.get("rules") or {}).items():
            yield tool, str(key), entry
        for entry in cfg.get("patterns") or []:
            yield tool, entry.get("match", "?"), entry


def test_every_entry_is_justified(raw):
    for tool, key, entry in _all_entries(raw):
        assert entry.get("source"), f"{tool}:{key} has no source"
        assert len(entry["source"]) > 20, f"{tool}:{key} source is too thin to audit"


def test_cwe_values_are_positive_ints_or_explicit_null(raw):
    for tool, key, entry in _all_entries(raw):
        assert "cwe" in entry, f"{tool}:{key} omits the cwe key"
        cwe = entry["cwe"]
        assert cwe is None or (isinstance(cwe, int) and cwe > 0), f"{tool}:{key} cwe={cwe!r}"


def test_a_missing_cwe_key_is_a_load_error():
    """`cwe: null` is a decision; omitting the key is an unfinished entry."""
    with pytest.raises(ValueError, match="no 'cwe' key"):
        CweTable({"version": 1, "tools": {"x": {"rules": {"a": {"source": "forgot the cwe"}}}}})


def test_every_mapped_cwe_is_in_the_catalog_or_declared_out_of_it(raw, catalog_cwes):
    declared = {int(k) for k in (raw.get("out_of_catalog") or {})}
    for tool, key, entry in _all_entries(raw):
        cwe = entry.get("cwe")
        if cwe is None:
            continue
        assert cwe in catalog_cwes or cwe in declared, (
            f"{tool}:{key} maps to CWE-{cwe}, which no catalog class plants and which "
            f"is not listed under out_of_catalog. It can never score, so it is either "
            f"a typo or an undocumented decision."
        )


def test_out_of_catalog_entries_are_really_out_of_catalog(raw, catalog_cwes):
    """Stale exemptions are as misleading as missing ones."""
    for cwe, reason in (raw.get("out_of_catalog") or {}).items():
        assert len(str(reason)) > 20, f"out_of_catalog CWE-{cwe} has no real reason"
        if int(cwe) in catalog_cwes:
            pytest.fail(
                f"CWE-{cwe} is declared out of catalog but the taxonomy now plants it; "
                "remove the exemption."
            )


def test_skipped_entries_carry_no_cwe(raw):
    """`skip` means "this is not a claim about the target"; a CWE would contradict it."""
    for tool, key, entry in _all_entries(raw):
        if entry.get("skip"):
            assert entry.get("cwe") is None, f"{tool}:{key} is skipped but has a CWE"


def test_wapiti_table_covers_every_category_wapiti_can_emit(table):
    """wapiti publishes no CWE, so an unlisted category is a finding lost to 'unknown'."""
    mapped = set(table.rules("wapiti"))
    missing = WAPITI_CATEGORIES - mapped
    unknown = mapped - WAPITI_CATEGORIES
    assert not missing, f"wapiti categories missing from the table: {sorted(missing)}"
    assert not unknown, f"table has wapiti categories wapiti does not emit: {sorted(unknown)}"


def test_skipfish_table_covers_every_issue_code(table):
    """Same reasoning as wapiti: the numeric type is skipfish's entire taxonomy."""
    mapped = {int(k) for k in table.rules("skipfish")}
    expected = SKIPFISH_ISSUE_CODES | SKIPFISH_DEAD_CONSTANTS
    assert not (expected - mapped), f"skipfish codes missing: {sorted(expected - mapped)}"
    assert not (mapped - expected), f"skipfish codes that do not exist: {sorted(mapped - expected)}"
    assert 40303 not in mapped, "40303 does not exist in skipfish"


def test_skipfish_scanner_telemetry_band_is_skipped(table):
    """The 2xxxx band is skipfish reporting on its own scan, not on the target."""
    for code, rule in table.rules("skipfish").items():
        if code.startswith("2"):
            assert rule.skip, f"skipfish {code} is scanner telemetry and must be skipped"


def test_skipfish_severity_map_covers_the_five_psev_levels(raw):
    """'severity' in the .js files is PSEV(type)-1, i.e. 0..4 for PSEV_INFO..PSEV_HI."""
    severity_map = raw["tools"]["skipfish"]["severity_map"]
    assert set(severity_map) == {"0", "1", "2", "3", "4"}


def test_nikto_patterns_compile_and_are_ordered_specific_first(table):
    patterns = table.patterns("nikto")
    assert patterns, "nikto has no pattern table; every finding would be unmapped"
    for rule in patterns:
        assert rule.pattern is not None
        re.compile(rule.pattern.pattern)  # already compiled; assert it round-trips
    # The catch-all hedges must come last, or they would swallow specific matches.
    hedges = [i for i, r in enumerate(patterns) if "interesting" in r.key]
    assert not hedges or min(hedges) > len(patterns) // 2


def test_every_driver_has_a_section(table):
    """A tool with no section silently maps everything to null."""
    for tool in ("zap", "nuclei", "wapiti", "nikto", "skipfish", "generic"):
        assert table.tool_cfg(tool), f"no mapping section for {tool}"
