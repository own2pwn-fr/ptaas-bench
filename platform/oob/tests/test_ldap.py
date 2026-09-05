"""LDAP listener: BER round-trips for the two operations a JNDI lookup performs."""

from __future__ import annotations

import socket

from edge_resolver import ber


def _bind_request(dn: str, message_id: int = 1) -> bytes:
    op = ber.tlv(
        ber.APP_BIND_REQUEST,
        ber.enc_int(3) + ber.enc_str(dn) + ber.tlv(0x80, b""),  # [0] simple, empty password
    )
    return ber.enc_seq(ber.enc_int(message_id), op)


def _search_request(base: str, message_id: int = 2) -> bytes:
    op = ber.tlv(
        ber.APP_SEARCH_REQUEST,
        ber.enc_str(base)
        + ber.enc_enum(0)  # scope: baseObject
        + ber.enc_enum(0)  # derefAliases
        + ber.enc_int(0)  # sizeLimit
        + ber.enc_int(0)  # timeLimit
        + ber.tlv(ber.TAG_BOOLEAN, b"\x00")  # typesOnly
        + ber.tlv(0x87, b"objectClass")  # filter: (objectClass=*)
        + ber.enc_seq(),  # attributes: all
    )
    return ber.enc_seq(ber.enc_int(message_id), op)


def _exchange(port: int, *messages: bytes, expect: int = 1, timeout: float = 5.0) -> list[bytes]:
    """Send messages, read back ``expect`` LDAPMessages."""
    out: list[bytes] = []
    with socket.create_connection(("127.0.0.1", port), timeout=timeout) as sock:
        for message in messages:
            sock.sendall(message)
        buffer = b""
        while len(out) < expect:
            chunk = sock.recv(65536)
            if not chunk:
                break
            buffer += chunk
            while buffer:
                try:
                    element = ber.read_tlv(buffer)
                except ber.BerError:
                    break
                out.append(buffer[: element.end])
                buffer = buffer[element.end :]
    return out


def _result_code(message: bytes) -> tuple[int, int, int]:
    """Return (message_id, protocol op tag, resultCode)."""
    envelope = ber.read_tlv(message)
    parts = list(ber.iter_tlv(envelope.content))
    op = parts[1]
    fields = list(ber.iter_tlv(op.content))
    return parts[0].int, op.tag, fields[0].int


def test_bind_is_answered_success_and_the_dn_is_recorded(service):
    (response,) = _exchange(
        service.ports["ldap"], _bind_request("cn=shop0031,dc=edge,dc=internal")
    )
    message_id, tag, result = _result_code(response)
    assert (message_id, tag, result) == (1, ber.APP_BIND_RESPONSE, 0)

    (record,) = service.store.wait_for(1, timeout=5)
    assert (record.channel, record.token, record.source) == ("ldap", "shop0031", "ldap_dn")
    assert record.detail["operation"] == "bind"
    # No hostname travels over plain LDAP, so we do not pretend to know the zone.
    assert record.owned_zone is None


def test_search_returns_an_entry_then_done(service):
    """A JNDI client binds anonymously, then searches: it must get past both."""
    responses = _exchange(
        service.ports["ldap"],
        _bind_request(""),
        _search_request("shop0031"),
        expect=3,
    )
    assert len(responses) == 3
    assert _result_code(responses[0])[1] == ber.APP_BIND_RESPONSE
    entry = ber.read_tlv(responses[1])
    entry_op = list(ber.iter_tlv(entry.content))[1]
    assert entry_op.tag == ber.APP_SEARCH_ENTRY
    assert list(ber.iter_tlv(entry_op.content))[0].text == "shop0031"
    assert _result_code(responses[2]) == (2, ber.APP_SEARCH_DONE, 0)

    records = service.store.wait_for(2, timeout=5)
    assert [c.detail["operation"] for c in records] == ["bind", "search"]
    assert records[1].token == "shop0031"


def test_search_entry_carries_no_jndi_reference(service):
    """We record requests; we do not help a client load remote code."""
    responses = _exchange(service.ports["ldap"], _search_request("shop0031"), expect=2)
    assert b"javaCodebase" not in responses[0]
    assert b"javaSerializedData" not in responses[0]
    assert b"javaClassName" not in responses[0]


def test_dynamic_form_in_the_dn(service):
    _exchange(service.ports["ldap"], _search_request("cn=shop0031-9f2c,dc=example"), expect=2)
    records = service.store.wait_for(1, timeout=5)
    assert (records[0].token, records[0].nonce) == ("shop0031", "9f2c")


def test_token_outside_the_allowlist_is_recorded_as_unknown(make_service):
    """With BENCH_OOB_KNOWN_TOKENS set, a token-shaped DN we never planted is still
    stored -- it means the tool is testing with a collaborator token of its own."""
    service = make_service(known_tokens=frozenset({"shop0031"}))
    _exchange(service.ports["ldap"], _bind_request("cn=x7d9k2,dc=example,dc=net"))
    (record,) = service.store.wait_for(1, timeout=5)
    assert record.token == "x7d9k2" and record.known is False


def test_garbage_is_recorded_not_crashed(service):
    with socket.create_connection(("127.0.0.1", service.ports["ldap"]), timeout=5) as sock:
        sock.sendall(b"\x30\x05hello")
        sock.recv(4096)
    (record,) = service.store.wait_for(1, timeout=5)
    assert record.channel == "ldap" and record.token is None


def test_unbind_closes_without_a_response(service):
    unbind = ber.enc_seq(ber.enc_int(3), ber.tlv(ber.APP_UNBIND_REQUEST, b""))
    with socket.create_connection(("127.0.0.1", service.ports["ldap"]), timeout=5) as sock:
        sock.sendall(unbind)
        assert sock.recv(4096) == b""  # connection closed, no response defined
    # An LDAP connection that carried no DN is still worth one line of evidence.
    (record,) = service.store.wait_for(1, timeout=5)
    assert record.detail["empty"] is True
