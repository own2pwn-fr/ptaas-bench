"""Token grammar and extraction priority -- the rules the whole component hangs on."""

from __future__ import annotations

import pytest

from edge_resolver.tokens import (
    Candidate,
    address_parts,
    dn_values,
    extract,
    first_path_segment,
    host_label,
    parse_token,
    query_token,
)

ZONE = "telemetry-edge.net"


@pytest.mark.parametrize(
    "value,expected",
    [
        ("shop0031", ("shop0031", None)),
        ("SHOP0031", ("shop0031", None)),  # DNS and Host are case-insensitive
        ("abcd", ("abcd", None)),
        ("a" * 32, ("a" * 32, None)),
        ("shop0031-9f2c", ("shop0031", "9f2c")),
        ("shop0031-run7-attempt2", ("shop0031", "run7-attempt2")),  # nonce may hold dashes
    ],
)
def test_parse_token_accepts(value, expected):
    assert parse_token(value) == expected


@pytest.mark.parametrize(
    "value",
    ["abc", "a" * 33, "shop_0031", "shop.0031", "", None, "-shop0031", "shop0031-"],
)
def test_parse_token_rejects(value):
    assert parse_token(value) is None


def test_extraction_priority_is_global_and_fixed():
    """All six sources present at once: the leftmost DNS label must win, then Host,
    then path, then query, then SMTP localpart, then the LDAP DN."""
    everything = [
        Candidate("dns_label", "aaaa0001"),
        Candidate("host_header", "bbbb0002"),
        Candidate("path_segment", "cccc0003"),
        Candidate("query_t", "dddd0004"),
        Candidate("smtp_localpart", "eeee0005"),
        Candidate("ldap_dn", "ffff0006"),
    ]
    expected = ["aaaa0001", "bbbb0002", "cccc0003", "dddd0004", "eeee0005", "ffff0006"]
    for index, token in enumerate(expected):
        result = extract(everything[index:])
        assert (result.token, result.source) == (token, everything[index].source)


def test_extraction_skips_candidates_that_are_not_tokens():
    result = extract([Candidate("dns_label", "www"), Candidate("path_segment", "shop0031")])
    assert (result.token, result.source) == ("shop0031", "path_segment")


def test_extraction_keeps_the_best_candidate_when_nothing_parses():
    result = extract([Candidate("dns_label", "www"), Candidate("path_segment", "x")])
    assert result.token is None and result.candidate == "www" and not result.found


def test_extraction_reports_the_dynamic_form_verbatim():
    result = extract([Candidate("dns_label", "shop0031-9f2c")])
    assert (result.token, result.nonce, result.label) == ("shop0031", "9f2c", "shop0031-9f2c")


@pytest.mark.parametrize(
    "host,expected",
    [
        ("shop0031.telemetry-edge.net", "shop0031"),
        ("SHOP0031.OOB.BENCH.LOCAL.", "shop0031"),
        ("shop0031-9f2c.deep.telemetry-edge.net", "shop0031-9f2c"),
        ("telemetry-edge.net", None),  # the bare zone carries no token
        ("shop0031.telemetry-edge.net:8080", "shop0031"),  # Host header with a port
        ("x7d9k2.example-collab.net", "x7d9k2"),  # a host the tool chose
        ("127.0.0.1", "127"),  # nonsense, but never token-shaped, so harmless
        ("", None),
    ],
)
def test_host_label(host, expected):
    assert host_label(host, ZONE) == expected


def test_path_and_query_helpers():
    assert first_path_segment("/shop0031/x") == "shop0031"
    assert first_path_segment("//shop0031") == "shop0031"
    assert first_path_segment("/") is None
    assert query_token("a=1&t=shop0031&b=2") == "shop0031"
    assert query_token("a=1") is None


def test_address_and_dn_helpers():
    assert address_parts("<shop0031@telemetry-edge.net>") == ("shop0031", "telemetry-edge.net")
    assert address_parts("shop0031") == ("shop0031", None)
    assert dn_values("cn=shop0031,dc=oob,dc=bench") == ["shop0031", "oob", "bench"]
    assert dn_values("shop0031") == ["shop0031"]
