"""The generic ingester: a vendor's own export, JSON or SARIF."""

from __future__ import annotations

import pytest

from runners._lib.normalise import normalise_generic


def test_vendor_json_key_aliases(fixtures, table):
    """Vendors each spell the same field differently; renaming is their call, not ours."""
    result = normalise_generic(fixtures / "generic-vendor.json", table=table)
    sqli = next(f for f in result.findings if f.cwe == 89)
    assert sqli.url == "http://shopfront-web:3000/api/products?q=1"
    assert sqli.method == "GET"
    assert sqli.param == "q"
    assert sqli.severity == "critical"
    assert sqli.confidence == "high"  # "firm"
    cookie = next(f for f in result.findings if f.cwe == 614)
    assert cookie.severity == "low"


def test_vendor_finding_without_a_cwe_is_never_guessed_from_its_title(fixtures, table):
    """"Business logic flaw in checkout" could be five different CWEs. Null it is."""
    result = normalise_generic(fixtures / "generic-vendor.json", table=table)
    logic = next(f for f in result.findings if "Business logic" in f.name)
    assert logic.cwe is None
    assert any(u["key"] == "ACME-3" for u in result.unmapped)


def test_sarif_webrequest_and_cwe_tag(fixtures, table):
    result = normalise_generic(fixtures / "generic-vendor.sarif", table=table)
    xss = next(f for f in result.findings if f.cwe == 79)
    assert xss.url == "http://legacy-web/search.php"
    assert xss.method == "GET"
    assert xss.param == "term"
    # security-severity 7.5 is a CVSS-like score, not SARIF's error/warning/note.
    assert xss.severity == "high"


def test_sarif_result_without_a_cwe_tag(fixtures, table):
    result = normalise_generic(fixtures / "generic-vendor.sarif", table=table)
    banner = next(f for f in result.findings if "Apache" in f.name)
    assert banner.cwe is None
    assert banner.severity == "info"
    assert banner.url == "http://legacy-web/"


def test_the_dispatch_table_covers_every_shipped_driver():
    """A driver added without a parser would silently normalise to nothing."""
    from runners._lib.normalise import PARSERS

    assert set(PARSERS) == {"zap", "nuclei", "wapiti", "nikto", "skipfish", "generic"}


def test_dispatch_rejects_an_unknown_tool(tmp_path):
    from runners._lib.normalise import normalise

    with pytest.raises(KeyError, match="no parser for tool"):
        normalise("burpsuite", tmp_path / "x.json")
