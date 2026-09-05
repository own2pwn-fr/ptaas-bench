"""ZAP traditional-json normalisation."""

from __future__ import annotations

from runners._lib.normalise import CweTable, normalise_zap


def test_one_finding_per_instance(fixtures, table):
    """An alert with N instances becomes N findings.

    Collapsing them would discard the evidence that the tool found the same class
    on several endpoints, which is precisely what crawl coverage measures.
    """
    result = normalise_zap(fixtures / "zap-report.json", table=table)
    sqli = [f for f in result.findings if f.cwe == 89]
    assert len(sqli) == 2
    assert {f.url for f in sqli} == {
        "http://shopfront-web:3000/api/products?q=laptop%27+OR+%271%27%3D%271",
        "http://shopfront-web:3000/api/orders?status=open",
    }
    assert {f.method for f in sqli} == {"GET", "POST"}
    assert {f.param for f in sqli} == {"q", "status"}


def test_string_typed_numbers_are_coerced(fixtures, table):
    """ZAP emits riskcode/confidence/cweid as JSON strings (XML heritage)."""
    result = normalise_zap(fixtures / "zap-report.json", table=table)
    sqli = next(f for f in result.findings if f.cwe == 89)
    assert isinstance(sqli.cwe, int)
    assert sqli.severity == "high"       # riskcode "3"
    assert sqli.confidence == "medium"   # confidence "2"


def test_cweid_zero_is_null_not_cwe_zero(fixtures, table):
    """cweid "0" is ZAP's "no CWE" sentinel and must not become CWE-0."""
    result = normalise_zap(fixtures / "zap-report.json", table=table)
    timestamp = next(f for f in result.findings if f.name.startswith("Timestamp"))
    assert timestamp.cwe is None
    assert timestamp.severity == "info"
    # An empty param means "not applicable", not a parameter called "".
    assert timestamp.param is None
    assert any(u["key"] == "10096" for u in result.unmapped)


def test_raw_ref_points_back_into_the_report(fixtures, table):
    result = normalise_zap(fixtures / "zap-report.json", table=table)
    assert result.findings[0].raw_ref == "zap-report.json#site[0].alerts[0].instances[0]"


def test_per_plugin_override_beats_the_tools_own_cwe(fixtures):
    """The override mechanism exists for alerts where ZAP uses a CWE the catalog does not."""
    custom = CweTable(
        {
            "version": 99,
            "tools": {
                "zap": {
                    "null_values": [0, "0"],
                    "rules": {"40018": {"cwe": 943, "source": "test override"}},
                }
            },
        }
    )
    result = normalise_zap(fixtures / "zap-report.json", table=custom)
    assert {f.cwe for f in result.findings if f.name == "SQL Injection"} == {943}


def test_missing_file_is_not_an_exception(tmp_path, table):
    assert normalise_zap(tmp_path / "absent.json", table=table).findings == []
