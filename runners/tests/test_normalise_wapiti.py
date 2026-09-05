"""wapiti JSON normalisation. wapiti emits no CWE at all, so the table is everything."""

from __future__ import annotations

from runners._lib.normalise import normalise_wapiti


def test_categories_map_through_the_table(fixtures, table):
    result = normalise_wapiti(fixtures / "wapiti-report.json", table=table)
    by_name = {f.name: f for f in result.findings}
    assert by_name["SQL Injection"].cwe == 89
    assert by_name["SQL Injection"].param == "q"
    assert by_name["SQL Injection"].severity == "critical"  # level 4
    assert by_name["Reflected Cross Site Scripting"].cwe == 79
    assert by_name["Reflected Cross Site Scripting"].severity == "medium"  # level 2


def test_empty_categories_produce_nothing(fixtures, table):
    """Every registered category is pre-created as an empty list in a real report."""
    result = normalise_wapiti(fixtures / "wapiti-report.json", table=table)
    assert "Backup file" not in {f.name for f in result.findings}


def test_anomalies_and_additionals_are_not_findings(fixtures, table):
    """A 500 or a technology fingerprint is not a claim that a flaw exists.

    Counting them would inflate wapiti's false-positive rate with claims it never
    made.
    """
    result = normalise_wapiti(fixtures / "wapiti-report.json", table=table)
    names = {f.name for f in result.findings}
    assert "Internal Server Error" not in names
    assert "Fingerprint web technology" not in names


def test_reference_url_fallback_for_an_unknown_category(fixtures, table):
    """Unknown category + exactly one cwe.mitre.org reference -> that CWE."""
    result = normalise_wapiti(fixtures / "wapiti-report.json", table=table)
    quantum = next(f for f in result.findings if f.name == "Quantum Flux Injection")
    assert quantum.cwe == 943


def test_unmappable_category_is_null_and_recorded(fixtures, table):
    result = normalise_wapiti(fixtures / "wapiti-report.json", table=table)
    mystery = next(f for f in result.findings if f.name == "Mysterious Anomaly")
    assert mystery.cwe is None
    assert any(u["key"] == "Mysterious Anomaly" for u in result.unmapped)


def test_declared_null_category_is_null_without_guessing(fixtures, table):
    """A fingerprint category is mapped to null *deliberately*, with a source."""
    result = normalise_wapiti(fixtures / "wapiti-report.json", table=table)
    fingerprint = next(
        f for f in result.findings if f.name == "Fingerprint web application framework"
    )
    assert fingerprint.cwe is None
    # It is a known category, so it is not noise on the unmapped to-do list.
    assert not any(u["key"] == "Fingerprint web application framework" for u in result.unmapped)


def test_no_confidence_is_invented(fixtures, table):
    result = normalise_wapiti(fixtures / "wapiti-report.json", table=table)
    assert all(f.confidence is None for f in result.findings)
