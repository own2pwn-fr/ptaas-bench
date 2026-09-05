"""nuclei JSONL normalisation."""

from __future__ import annotations

from runners._lib.normalise import normalise_nuclei


def test_classification_cwe_is_used_first(fixtures, table):
    """info.classification.cwe-id is a lowercased list: ["cwe-89"] -> 89."""
    result = normalise_nuclei(fixtures / "nuclei.jsonl", table=table)
    sqli = next(f for f in result.findings if f.name.startswith("Error based"))
    assert sqli.cwe == 89
    assert sqli.severity == "critical"


def test_fuzzing_fields_name_the_injection_point(fixtures, table):
    """DAST results carry fuzzing_parameter/fuzzing_method; nothing else names the param."""
    result = normalise_nuclei(fixtures / "nuclei.jsonl", table=table)
    sqli = next(f for f in result.findings if f.cwe == 89)
    assert sqli.param == "q"
    assert sqli.method == "GET"
    assert sqli.url == "http://shopfront-web:3000/api/products?q=laptop%27"


def test_tag_fallback_only_for_unambiguous_tags(fixtures, table):
    result = normalise_nuclei(fixtures / "nuclei.jsonl", table=table)
    xss = next(f for f in result.findings if f.name == "Reflected Cross Site Scripting")
    assert xss.cwe == 79
    # The verb comes from the raw request nuclei echoes back, not from a guess.
    assert xss.method == "POST"


def test_fingerprint_template_is_a_declared_null_not_a_to_do(fixtures, table):
    """A `tech` template makes no weakness claim, and the table says so explicitly.

    It comes out as cwe null but does NOT land on the unmapped list: that list is
    the set of outputs nobody has ruled on yet, and padding it with settled cases
    is how it stops being read.
    """
    result = normalise_nuclei(fixtures / "nuclei.jsonl", table=table)
    nginx = next(f for f in result.findings if f.name == "nginx version detect")
    assert nginx.cwe is None
    assert not any(u["key"] == "nginx-version" for u in result.unmapped)


def test_template_nobody_has_ruled_on_lands_on_the_to_do_list(tmp_path, table):
    import json

    path = tmp_path / "nuclei-x.jsonl"
    path.write_text(
        json.dumps(
            {
                "template-id": "some-unreviewed-template",
                "info": {"name": "Unreviewed", "tags": ["unreviewed-tag"], "severity": "high"},
                "type": "http",
                "matched-at": "http://legacy-web/thing",
                "matcher-status": True,
            }
        )
        + "\n"
    )
    result = normalise_nuclei(path, table=table)
    assert result.findings[0].cwe is None
    assert [u["key"] for u in result.unmapped] == ["some-unreviewed-template"]


def test_truncated_last_line_is_skipped_not_fatal(fixtures, table):
    """A scan killed at the budget deadline leaves half a JSON line behind."""
    result = normalise_nuclei(fixtures / "nuclei.jsonl", table=table)
    assert len(result.findings) == 3
    assert all(f.raw_ref.startswith("nuclei.jsonl#L") for f in result.findings)
