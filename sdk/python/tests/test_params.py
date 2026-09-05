"""Unit tests for input enumeration (hashing, truncation, flattening, parsing)."""

from __future__ import annotations

import hashlib

from ptaas_bench_sdk._params import (
    ParamCollector,
    collect_body,
    collect_headers,
    describe_param,
    flatten_json,
    is_injectable_header,
    iter_multipart,
    parse_cookie_header,
)


def test_describe_param_hashes_raw_value_and_truncates_sample():
    value = "A" * 400
    entry = describe_param("q", "query", value)
    assert entry == {
        "name": "q",
        "in": "query",
        "value_sha256": hashlib.sha256(value.encode()).hexdigest(),
        "value_len": 400,
        "sample": "A" * 256,
    }
    assert len(entry["sample"]) == 256


def test_hash_matches_catalog_default_value():
    # The scorer compares this hash with BENCH-SHOP-0001's default_value "laptop" to
    # tell "visited" from "fuzzed"; the encoding must not drift.
    assert describe_param("q", "query", "laptop")["value_sha256"] == hashlib.sha256(b"laptop").hexdigest()


def test_json_leaves_hash_like_their_wire_form():
    leaves = dict(flatten_json({"id": 1001, "ok": True, "none": None}))
    assert leaves == {"id": "1001", "ok": "true", "none": "null"}


def test_flatten_json_uses_dotted_paths_including_list_indices():
    document = {"filter": {"tags": ["a", "b"], "page": {"size": 10}}, "empty": {}, "none_list": []}
    assert dict(flatten_json(document)) == {
        "filter.tags.0": "a",
        "filter.tags.1": "b",
        "filter.page.size": "10",
        "empty": "{}",
        "none_list": "[]",
    }


def test_flatten_json_is_depth_bounded():
    document: dict = {}
    node = document
    for _ in range(80):
        child: dict = {}
        node["n"] = child
        node = child
    node["leaf"] = "x"
    assert list(flatten_json(document)) == []  # deeper than JSON_DEPTH_MAX: nothing yielded


def test_collector_dedupes_identical_repeats_but_keeps_polluted_values():
    collector = ParamCollector()
    collector.add("q", "query", "laptop")
    collector.add("q", "query", "laptop")
    collector.add("q", "query", "' OR 1=1--")
    names = [(e["name"], e["sample"]) for e in collector.entries]
    assert names == [("q", "laptop"), ("q", "' OR 1=1--")]


def test_collector_is_bounded():
    collector = ParamCollector(max_params=5)
    for index in range(50):
        collector.add(f"p{index}", "query", str(index))
    assert len(collector.entries) == 5
    assert collector.truncated is True


def test_header_allowlist_covers_x_prefixed_and_named_headers():
    for name in ("host", "referer", "user-agent", "origin", "content-type", "x-forwarded-for", "x-tenant"):
        assert is_injectable_header(name)
    assert not is_injectable_header("accept-encoding")


def test_collect_headers_splits_cookies():
    collector = ParamCollector()
    collect_headers(collector, [("cookie", "session=abc; role=admin"), ("accept", "*/*"), ("x-tenant", "42")])
    got = {(e["in"], e["name"], e["sample"]) for e in collector.entries}
    assert ("cookie", "session", "abc") in got
    assert ("cookie", "role", "admin") in got
    assert ("header", "x-tenant", "42") in got
    assert not any(e["name"] == "accept" for e in collector.entries)


def test_cookie_parser_keeps_malformed_payloads():
    # SimpleCookie would silently drop this; an injected cookie is exactly what must
    # not be dropped.
    assert list(parse_cookie_header("a=1; weird value; b=' OR 1=1")) == [
        ("a", "1"),
        ("weird value", ""),
        ("b", "' OR 1=1"),
    ]


def test_collect_body_json_and_graphql_detection():
    collector = ParamCollector()
    body = b'{"query":"{me{id}}","variables":{"id":"7"},"operationName":"Me"}'
    collect_body(collector, body, "application/json")
    graphql = {e["name"]: e["sample"] for e in collector.entries if e["in"] == "graphql"}
    assert graphql == {"query": "{me{id}}", "variables.id": "7", "operationName": "Me"}
    assert any(e["in"] == "json" and e["name"] == "variables.id" for e in collector.entries)


def test_collect_body_form_and_unparseable():
    collector = ParamCollector()
    collect_body(collector, b"user=admin&pw=%27+OR+1", "application/x-www-form-urlencoded")
    assert {(e["name"], e["sample"]) for e in collector.entries} == {("user", "admin"), ("pw", "' OR 1")}

    raw = ParamCollector()
    collect_body(raw, b"<xml/>", "application/xml")
    assert raw.entries[0]["in"] == "raw" and raw.entries[0]["name"] == "body"

    broken = ParamCollector()
    collect_body(broken, b"{not json", "application/json")
    assert broken.entries[0]["in"] == "raw"


def test_multipart_yields_field_names_and_filenames():
    body = (
        b"--BB\r\nContent-Disposition: form-data; name=\"note\"\r\n\r\nhello\r\n"
        b"--BB\r\nContent-Disposition: form-data; name=\"doc\"; filename=\"../../etc/passwd\"\r\n"
        b"Content-Type: text/plain\r\n\r\nPAYLOAD\r\n--BB--\r\n"
    )
    parsed = dict(iter_multipart(body, "multipart/form-data; boundary=BB"))
    assert parsed["note"] == b"hello"
    assert parsed["doc"] == b"PAYLOAD"
    assert parsed["doc.filename"] == "../../etc/passwd"


def test_multipart_survives_a_truncated_body():
    body = b"--BB\r\nContent-Disposition: form-data; name=\"a\"\r\n\r\nvalue"
    assert dict(iter_multipart(body, "multipart/form-data; boundary=BB")) == {"a": b"value"}
