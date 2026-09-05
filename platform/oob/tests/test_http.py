"""HTTP and HTTPS listeners, and the identifier rules that apply to them."""

from __future__ import annotations

import socket

from conftest import ZONE, http_request


def test_host_header_outranks_path_and_query(service):
    response = http_request(
        service.ports["http"], "/cccc0003/x?t=dddd0004", host=f"bbbb0002.{ZONE}"
    )
    assert response.startswith(b"HTTP/1.1 200 OK")

    (record,) = service.store.wait_for(1, timeout=5)
    assert (record.channel, record.token, record.source) == ("http", "bbbb0002", "host_header")
    assert record.owned_zone is True
    assert record.detail["method"] == "GET"


def test_external_host_is_recorded_with_its_name(service):
    """The ordinary case now: a fetch aimed at a host the tool minted."""
    http_request(service.ports["http"], "/catalog.json", host="z9x2k1p8.example-collab.net")
    (record,) = service.store.wait_for(1, timeout=5)
    assert record.host == "z9x2k1p8.example-collab.net"
    assert record.owned_zone is False and record.known is False


def test_path_segment_used_when_the_host_is_the_bare_zone(service):
    http_request(service.ports["http"], "/shop0031/catalog.json?t=dddd0004", host=ZONE)
    (record,) = service.store.wait_for(1, timeout=5)
    assert (record.token, record.source) == ("shop0031", "path_segment")


def test_query_parameter_is_the_last_http_resort(service):
    http_request(service.ports["http"], "/x?t=shop0031", host=ZONE)
    (record,) = service.store.wait_for(1, timeout=5)
    assert (record.token, record.source) == ("shop0031", "query_t")


def test_dynamic_form_over_http(service):
    http_request(service.ports["http"], "/x", host=f"shop0031-9f2c.{ZONE}")
    (record,) = service.store.wait_for(1, timeout=5)
    assert (record.token, record.nonce) == ("shop0031", "9f2c")


def test_absolute_form_target_is_understood(service):
    """Server-side request payloads frequently arrive proxy-style."""
    http_request(service.ports["http"], f"http://shop0031.{ZONE}/x", host="proxy.invalid")
    (record,) = service.store.wait_for(1, timeout=5)
    assert (record.token, record.source) == ("shop0031", "host_header")


def test_request_without_any_identifier_is_still_recorded(service):
    http_request(service.ports["http"], "/", host="127.0.0.1")
    (record,) = service.store.wait_for(1, timeout=5)
    assert record.token is None and record.known is False
    assert record.source_ip == "127.0.0.1"


def test_non_http_bytes_are_recorded(service):
    with socket.create_connection(("127.0.0.1", service.ports["http"]), timeout=5) as sock:
        sock.sendall(b"\x00\x01\x02 not http at all\r\n\r\n")
        assert sock.recv(4096).startswith(b"HTTP/1.1 400")
    (record,) = service.store.wait_for(1, timeout=5)
    assert record.detail["malformed"] is True and record.channel == "http"


def test_https_listener_records_on_its_own_channel(service):
    assert service.tls_error is None
    response = http_request(
        service.ports["https"], "/x", host=f"shop0031.{ZONE}", tls=True
    )
    assert response.startswith(b"HTTP/1.1 200 OK")
    (record,) = service.store.wait_for(1, timeout=5)
    assert (record.channel, record.token) == ("https", "shop0031")


def test_tls_server_name_is_recovered_when_the_host_header_is_useless(service):
    """A client that connects by name but sends a bare address in Host still tells us
    which host it wanted, through SNI."""
    http_request(
        service.ports["https"],
        "/x",
        host="127.0.0.1",
        server_name="z9x2k1p8.example-collab.net",
        tls=True,
    )
    (record,) = service.store.wait_for(1, timeout=5)
    assert record.token == "z9x2k1p8"


def test_head_returns_no_body_but_the_length(service):
    response = http_request(service.ports["http"], "/asset.js", host=ZONE, method="HEAD")
    head, _, body = response.partition(b"\r\n\r\n")
    assert b"Content-Length: 3" in head and body == b""


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
    (record,) = service.store.wait_for(1, timeout=5)
    assert record.detail["body_len"] == 107 and record.token == "shop0031"


def test_tls_connection_that_never_speaks_http_is_still_recorded(service):
    """A payload that opens TLS to a host and then sends nothing usable has still proved
    the fetch; the negotiated server name is the join key, so it must survive."""
    import socket
    import ssl

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    with socket.create_connection(("127.0.0.1", service.ports["https"]), timeout=5) as raw:
        with context.wrap_socket(raw, server_hostname="z9x2k1p8.example-collab.net") as tls:
            tls.sendall(b"\x16\x03\x01 garbage\r\n\r\n")
            tls.recv(4096)

    (record,) = service.store.wait_for(1, timeout=5)
    assert record.channel == "https"
    assert record.host == "z9x2k1p8.example-collab.net"
    assert record.detail["malformed"] is True
