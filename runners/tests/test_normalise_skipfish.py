"""skipfish normalisation: its report is JavaScript, and its taxonomy is numeric."""

from __future__ import annotations

from runners._lib.normalise import normalise_skipfish, parse_js_assignments


def test_js_literal_parser_handles_single_quotes_and_escapes(fixtures):
    doc = parse_js_assignments((fixtures / "skipfish-report" / "samples.js").read_text())
    assert set(doc) == {"mime_samples", "issue_samples"}
    leftover = doc["issue_samples"][3]["samples"][0]
    assert leftover["extra"] == "It's a leftover"


def test_numeric_issue_types_map_through_the_table(fixtures, table):
    result = normalise_skipfish(fixtures / "skipfish-report", table=table)
    by_url = {f.url: f for f in result.findings}
    shell = by_url["http://legacy-web/cgi-bin/ping.cgi?host=localhost"]
    assert shell.cwe == 78          # type 50102, PROB_SH_INJECT
    assert shell.param == "host"
    assert shell.severity == "high"  # 'severity' 4 == PSEV_HI
    xss = by_url["http://legacy-web/search.php?term=x"]
    assert xss.cwe == 79            # type 40101
    assert xss.severity == "medium"  # 'severity' 3 == PSEV_MED


def test_scanner_telemetry_is_not_a_finding(fixtures, table):
    """The 2xxxx band is skipfish reporting on its own scan, not on the target.

    Emitting it would credit skipfish with false positives it never claimed.
    """
    result = normalise_skipfish(fixtures / "skipfish-report", table=table)
    assert all("slow.php" not in (f.url or "") for f in result.findings)


def test_no_method_is_invented(fixtures, table):
    """skipfish reports no HTTP verb anywhere; null beats a plausible-looking GET."""
    result = normalise_skipfish(fixtures / "skipfish-report", table=table)
    assert all(f.method is None for f in result.findings)


def test_extra_is_only_trusted_when_the_url_confirms_it(fixtures, table):
    """'extra' holds a parameter name on injection issues and free text elsewhere."""
    result = normalise_skipfish(fixtures / "skipfish-report", table=table)
    leftover = next(f for f in result.findings if "README.old" in (f.url or ""))
    assert leftover.param is None


def test_unknown_type_is_null_and_recorded(fixtures, table):
    result = normalise_skipfish(fixtures / "skipfish-report", table=table)
    unknown = next(f for f in result.findings if "unknown-type" in (f.url or ""))
    assert unknown.cwe is None
    assert any(u["key"] == "60101" for u in result.unmapped)
