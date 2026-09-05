"""nikto normalisation, JSON (2.6.x) and XML."""

from __future__ import annotations

from runners._lib.normalise import normalise_nikto


def test_prose_is_matched_by_the_ordered_pattern_table(fixtures, table):
    result = normalise_nikto(fixtures / "nikto-report.json", table=table)
    listing = next(f for f in result.findings if "Directory indexing" in f.name)
    assert listing.cwe == 548
    assert listing.url == "http://legacy-web/uploads/"
    assert listing.method == "GET"


def test_nikto_own_reference_cwe_wins(fixtures, table):
    """~4% of nikto's tests state a CWE in the free-text references column."""
    result = normalise_nikto(fixtures / "nikto-report.json", table=table)
    git = next(f for f in result.findings if ".git" in f.url)
    # CWE-527 (exposed VCS) is what the pattern table says; nikto's own reference
    # says CWE-552. The pattern wins because it is the more specific statement and
    # is what the catalog plants -- and the ordering is deliberate, not accidental.
    assert git.cwe == 527


def test_reference_cwe_used_when_no_pattern_matches(fixtures, table):
    """A finding nothing matches, but whose references name a CWE, uses that CWE."""
    from runners._lib.normalise import CweTable

    bare = CweTable({"version": 99, "tools": {"nikto": {"use_reference_cwe": True}}})
    result = normalise_nikto(fixtures / "nikto-report.json", table=bare)
    git = next(f for f in result.findings if ".git" in f.url)
    assert git.cwe == 552


def test_deliberate_null_and_unknown_null(fixtures, table):
    result = normalise_nikto(fixtures / "nikto-report.json", table=table)
    header = next(f for f in result.findings if "x-powered-by" in f.name.lower())
    unknown = next(f for f in result.findings if "unclassified script" in f.name)
    assert header.cwe is None and unknown.cwe is None
    # The header observation is a known-null; the unclassified script is genuinely
    # unknown and must show up on the maintenance list.
    keys = {u["key"] for u in result.unmapped}
    assert "999103" in keys
    assert "999986" not in keys


def test_no_severity_is_invented_where_the_table_has_none(fixtures, table):
    """nikto grades nothing; a severity only appears when the mapping rule sets one."""
    result = normalise_nikto(fixtures / "nikto-report.json", table=table)
    unknown = next(f for f in result.findings if "unclassified script" in f.name)
    assert unknown.severity is None
    listing = next(f for f in result.findings if "Directory indexing" in f.name)
    assert listing.severity == "medium"


def test_xml_report_is_parsed_too(fixtures, table):
    result = normalise_nikto(fixtures / "nikto-report.xml", table=table)
    urls = {f.url for f in result.findings}
    assert "http://legacy-web/uploads/" in urls
    clickjacking = next(f for f in result.findings if f.cwe == 1021)
    assert clickjacking.url == "http://legacy-web/"


def test_xml_informational_item_without_uri_is_tolerated(fixtures, table):
    """Informational items carry only <description>: no method, no uri, no id links."""
    result = normalise_nikto(fixtures / "nikto-report.xml", table=table)
    multi = next(f for f in result.findings if "Multiple index files" in f.name)
    assert multi.cwe is None
    assert multi.url == "http://legacy-web/"
