"""DNS listener: wire round-trips against the real socket, on an ephemeral port."""

from __future__ import annotations

import socket
import struct

from bench_oob import dnswire

from conftest import ZONE, build_dns_query, dns_tcp, dns_udp, parse_dns_response


def test_udp_a_query_answers_and_records(service):
    response = parse_dns_response(dns_udp(service.ports["dns_udp"], f"shop0031.{ZONE}"))

    assert response["qr"] and response["aa"]
    assert response["rcode"] == dnswire.RCODE_NOERROR
    assert response["question"] == (f"shop0031.{ZONE}", dnswire.TYPE_A)
    # The A answer must be usable: a payload that resolves the name then connects to it
    # is how a DNS callback turns into an HTTP callback.
    assert [a["rdata"] for a in response["answers"]] == ["127.0.0.1"]

    (callback,) = service.store.wait_for(1, timeout=5)
    assert (callback.channel, callback.token, callback.source) == ("dns", "shop0031", "dns_label")
    assert callback.in_zone is True and callback.known is True
    assert callback.detail["proto"] == "udp"
    assert callback.source_ip == "127.0.0.1"


def test_tcp_query_round_trip(service):
    response = parse_dns_response(dns_tcp(service.ports["dns_tcp"], f"shop0031.{ZONE}"))
    assert [a["rdata"] for a in response["answers"]] == ["127.0.0.1"]

    (callback,) = service.store.wait_for(1, timeout=5)
    assert callback.detail["proto"] == "tcp" and callback.token == "shop0031"


def test_dynamic_token_form_is_split(service):
    dns_udp(service.ports["dns_udp"], f"shop0031-9f2c.{ZONE}")
    (callback,) = service.store.wait_for(1, timeout=5)
    assert (callback.token, callback.nonce, callback.raw_token) == (
        "shop0031",
        "9f2c",
        "shop0031-9f2c",
    )


def test_repeated_hits_stay_distinguishable(service):
    for nonce in ("aa1", "aa2"):
        dns_udp(service.ports["dns_udp"], f"shop0031-{nonce}.{ZONE}")
    callbacks = service.store.wait_for(2, timeout=5)
    assert [c.token for c in callbacks] == ["shop0031", "shop0031"]
    assert [c.nonce for c in callbacks] == ["aa1", "aa2"]


def test_unsupported_type_gets_empty_noerror_with_soa(service):
    """AAAA must not be NXDOMAIN: a client that gives up on the name also gives up on A."""
    response = parse_dns_response(dns_udp(service.ports["dns_udp"], f"shop0031.{ZONE}", dnswire.TYPE_AAAA))
    assert response["rcode"] == dnswire.RCODE_NOERROR
    assert response["answers"] == [] and response["nscount"] == 1
    assert service.store.wait_for(1, timeout=5)[0].token == "shop0031"


def test_txt_query_is_answered(service):
    response = parse_dns_response(dns_udp(service.ports["dns_udp"], f"shop0031.{ZONE}", dnswire.TYPE_TXT))
    assert response["answers"] and response["answers"][0]["type"] == dnswire.TYPE_TXT


def test_out_of_zone_query_is_refused_but_still_recorded(service):
    """A tool resolving its own collaborator domain through us is data, not noise --
    but we must not act as an open resolver on bench-public."""
    response = parse_dns_response(dns_udp(service.ports["dns_udp"], "x7d9k2.collab.example"))
    assert response["rcode"] == dnswire.RCODE_REFUSED and response["answers"] == []

    (callback,) = service.store.wait_for(1, timeout=5)
    assert callback.token == "x7d9k2"  # extracted anyway
    assert callback.in_zone is False and callback.known is False


def test_malformed_datagram_is_recorded_and_answered(service):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(5)
    try:
        sock.sendto(b"\x12\x34not-a-dns-message", ("127.0.0.1", service.ports["dns_udp"]))
        data = sock.recv(4096)
    finally:
        sock.close()

    txid, flags = struct.unpack("!2H", data[:4])
    assert txid == 0x1234 and flags & 0x0F == dnswire.RCODE_FORMERR
    (callback,) = service.store.wait_for(1, timeout=5)
    assert callback.detail["malformed"] is True and callback.token is None


def test_transaction_id_and_question_are_echoed(service):
    query = build_dns_query(f"shop0031.{ZONE}", txid=0xBEEF)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(5)
    try:
        sock.sendto(query, ("127.0.0.1", service.ports["dns_udp"]))
        response = parse_dns_response(sock.recv(4096))
    finally:
        sock.close()
    assert response["txid"] == 0xBEEF
    assert response["question"] == (f"shop0031.{ZONE}", dnswire.TYPE_A)


def test_compressed_question_name_is_parsed():
    """Pointer handling, checked at the codec level: a malicious or exotic client can
    compress the question, and a naive parser would either crash or loop."""
    header = struct.pack("!6H", 1, 0x0100, 1, 0, 0, 0)
    # Put the real name at the end and point the question at it.
    name = dnswire.encode_name(f"shop0031.{ZONE}")
    padded = header + b"\xc0\x14" + struct.pack("!2H", 1, 1) + b"\x00\x00" + name
    query = dnswire.parse_query(padded)
    assert query.question.name == f"shop0031.{ZONE}"


def test_pointer_loop_is_rejected():
    header = struct.pack("!6H", 1, 0x0100, 1, 0, 0, 0)
    try:
        dnswire.parse_query(header + b"\xc0\x0c")
    except dnswire.DnsFormatError:
        return
    raise AssertionError("a compression pointer loop must not be accepted")
