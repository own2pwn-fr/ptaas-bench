"""HTTP and HTTPS listeners, and the token priority rules that apply to them."""

from __future__ import annotations

import socket

from conftest import ZONE, http_request


def test_host_header_outranks_path_and_query(service):
    response = http_request(
        service.ports["http"],
        "/cccc0003/x?t=dddd0004",
        host=f"bbbb0002.{ZONE}",
    )
    assert response.startswith(b"HTTP/1.1 200 OK")

    (callback,) = service.store.wait_for(1, timeout=5)
    assert (callback.channel, callback.token, callback.source) == (
        "http",
        "bbbb0002",
        "host_header",
    )
    assert callback.in_zone is True
    assert callback.detail["method"] == "GET"


def test_path_segment_used_when_the_host_is_the_bare_zone(service):
    http_request(service.ports["http"], "/shop0031/catalog.json?t=dddd0004", host=ZONE)
    (callback,) = service.store.wait_for(1, timeout=5)
    assert (callback.token, callback.source) == ("shop0031", "path_segment")


def test_query_parameter_is_the_last_http_resort(service):
    http_request(service.ports["http"], "/x?t=shop0031", host=ZONE)
    (callback,) = service.store.wait_for(1, timeout=5)
    assert (callback.token, callback.source) == ("shop0031", "query_t")


def test_dynamic_form_over_http(service):
    http_request(service.ports["http"], "/x", host=f"shop0031-9f2c.{ZONE}")
    (callback,) = service.store.wait_for(1, timeout=5)
    assert (callback.token, callback.nonce) == ("shop0031", "9f2c")


def test_absolute_form_target_is_understood(service):
    """SSRF payloads frequently arrive proxy-style: GET http://host/path HTTP/1.1."""
    http_request(
        service.ports["http"],
        f"http://shop0031.{ZONE}/x",
        host="proxy.invalid",
    )
    (callback,) = service.store.wait_for(1, timeout=5)
    assert (callback.token, callback.source) == ("shop0031", "host_header")


def test_foreign_host_is_recorded_as_unknown(service):
    http_request(service.ports["http"], "/", host="x7d9k2.collab.example")
    (callback,) = service.store.wait_for(1, timeout=5)
    assert callback.token == "x7d9k2"
    assert callback.in_zone is False and callback.known is False


def test_callback_without_any_token_is_still_recorded(service):
    http_request(service.ports["http"], "/", host="127.0.0.1")
    (callback,) = service.store.wait_for(1, timeout=5)
    assert callback.token is None and callback.known is False
    assert callback.source_ip == "127.0.0.1"
    assert "token=unknown" in callback.raw


def test_non_http_bytes_are_recorded(service):
    with socket.create_connection(("127.0.0.1", service.ports["http"]), timeout=5) as sock:
        sock.sendall(b"\x00\x01\x02 not http at all\r\n\r\n")
        assert sock.recv(4096).startswith(b"HTTP/1.1 400")
    (callback,) = service.store.wait_for(1, timeout=5)
    assert callback.detail["malformed"] is True and callback.channel == "http"


def test_https_listener_records_on_its_own_channel(service):
    assert service.https_error is None
    response = http_request(
        service.ports["https"], "/x", host=f"shop0031.{ZONE}", tls=True
    )
    assert response.startswith(b"HTTP/1.1 200 OK")
    (callback,) = service.store.wait_for(1, timeout=5)
    assert (callback.channel, callback.token) == ("https", "shop0031")


def test_body_is_read_but_only_its_size_is_kept(service):
    port = service.ports["http"]
    with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        body = b"secret=" + b"A" * 100
        head = (
            f"POST /shop0031 HTTP/1.1\r\nHost: {ZONE}\r\n"
            f"Content-Length: {len(body)}\r\n\r\n"
        ).encode()
        sock.sendall(head + body)
        assert sock.recv(4096).startswith(b"HTTP/1.1 200 OK")
    (callback,) = service.store.wait_for(1, timeout=5)
    assert callback.detail["body_len"] == 107 and callback.token == "shop0031"
