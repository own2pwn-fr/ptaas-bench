"""DNS listener: the catch-all answer, and the narrow paths that are not catch-all."""

from __future__ import annotations

import socket
import struct

from edge_resolver import dnswire

from conftest import ZONE, build_dns_query, dns_tcp, dns_udp, parse_dns_response


def test_any_external_name_resolves_and_is_recorded(service):
    """The heart of the design: a host the tool invented, in a zone we do not own, must
    resolve to us and be logged. Refusing it would score every blind defect as missed by
    every tool -- a fact about our network, not about the tool."""
    response = parse_dns_response(dns_udp(service.ports["dns_udp"], "z9x2k1p8.example-collab.net"))

    assert response["rcode"] == dnswire.RCODE_NOERROR
    assert response["a_records"] == ["127.0.0.1"]

    (record,) = service.store.wait_for(1, timeout=5)
    assert record.channel == "dns"
    assert record.host == "z9x2k1p8.example-collab.net"
    assert record.owned_zone is False
    assert record.token == "z9x2k1p8"  # the tool's own identifier, still worth grouping on
    assert record.source_ip == "127.0.0.1"


def test_deep_random_subdomain_also_resolves(service):
    name = "a1b2c3.d4e5.oast.example"
    assert parse_dns_response(dns_udp(service.ports["dns_udp"], name))["a_records"] == ["127.0.0.1"]
    assert service.store.wait_for(1, timeout=5)[0].host == name


def test_owned_zone_label_still_works(service):
    response = parse_dns_response(dns_udp(service.ports["dns_udp"], f"shop0031.{ZONE}"))
    assert response["aa"] and response["a_records"] == ["127.0.0.1"]
    (record,) = service.store.wait_for(1, timeout=5)
    assert (record.token, record.source, record.owned_zone) == ("shop0031", "dns_label", True)
    assert record.confidence == "high"  # a label in our own zone attributes itself


def test_tcp_query_round_trip(service):
    response = parse_dns_response(dns_tcp(service.ports["dns_tcp"], "z9x2k1p8.example-collab.net"))
    assert response["a_records"] == ["127.0.0.1"]
    assert service.store.wait_for(1, timeout=5)[0].detail["proto"] == "tcp"


def test_dynamic_token_form_is_split(service):
    dns_udp(service.ports["dns_udp"], f"shop0031-9f2c.{ZONE}")
    (record,) = service.store.wait_for(1, timeout=5)
    assert (record.token, record.nonce, record.raw_token) == ("shop0031", "9f2c", "shop0031-9f2c")


def test_mx_query_points_back_at_the_same_name(service):
    """So an application's MTA delivers to our SMTP listener instead of giving up."""
    response = parse_dns_response(
        dns_udp(service.ports["dns_udp"], "mail.example-collab.net", dnswire.TYPE_MX)
    )
    assert response["answers"] and response["answers"][0]["type"] == dnswire.TYPE_MX
    rdata = response["answers"][0]["rdata"]
    assert struct.unpack("!H", rdata[:2])[0] == 10
    assert b"example-collab" in rdata


def test_unsupported_type_in_our_zone_is_empty_noerror(service):
    response = parse_dns_response(dns_udp(service.ports["dns_udp"], f"shop0031.{ZONE}", dnswire.TYPE_AAAA))
    assert response["rcode"] == dnswire.RCODE_NOERROR
    assert response["answers"] == [] and response["nscount"] == 1


def test_internal_name_is_forwarded_upstream(make_service):
    """A target must still be able to resolve the reporting endpoint and its database.
    Getting this wrong breaks every application in the deployment."""
    seen: list[str] = []

    async def upstream(name: str):
        seen.append(name)
        return {"otel-collector": ("10.77.0.4",), "collector-db": ("10.77.0.3",)}.get(name, ())

    service = make_service(upstream=upstream, telemetry_url="http://otel-collector:8900")
    for name, expected in (("otel-collector", "10.77.0.4"), ("collector-db", "10.77.0.3")):
        response = parse_dns_response(dns_udp(service.ports["dns_udp"], name))
        assert response["a_records"] == [expected], name
        assert not response["aa"]  # forwarded, not ours to be authoritative for

    assert seen == ["otel-collector", "collector-db"]
    # Infrastructure chatter is not recorded: it would bury the requests that matter.
    assert len(service.store) == 0


def test_single_label_miss_falls_through_to_the_sinkhole(make_service):
    async def upstream(name: str):
        return ()

    service = make_service(upstream=upstream)
    response = parse_dns_response(dns_udp(service.ports["dns_udp"], "z9x2k1p8"))
    assert response["a_records"] == ["127.0.0.1"]
    (record,) = service.store.wait_for(1, timeout=5)
    assert record.host == "z9x2k1p8"


def test_known_internal_name_that_upstream_cannot_answer_gets_servfail(make_service):
    """Not our own address: pointing a database client at this process would turn a
    resolver blip into a silent misconnection."""

    async def upstream(name: str):
        return ()

    service = make_service(
        upstream=upstream,
        telemetry_url="http://otel-collector:8900",
        internal_names=("db.internal",),
    )
    response = parse_dns_response(dns_udp(service.ports["dns_udp"], "db.internal"))
    assert response["rcode"] == dnswire.RCODE_SERVFAIL
    assert response["answers"] == []


def test_external_names_are_never_forwarded(make_service):
    """No open resolver, no lookups leaking off the network, and no latency."""
    seen: list[str] = []

    async def upstream(name: str):
        seen.append(name)
        return ("1.2.3.4",)

    service = make_service(upstream=upstream)
    response = parse_dns_response(dns_udp(service.ports["dns_udp"], "z9x2k1p8.example-collab.net"))
    assert response["a_records"] == ["127.0.0.1"]
    assert seen == []


def test_a_hanging_upstream_does_not_hang_the_client(make_service):
    """An unanswered query costs a client its whole resolver timeout, and a captured
    callback would then look like an application error."""
    import asyncio
    import time

    async def upstream(name: str):
        await asyncio.sleep(30)
        return ()

    service = make_service(upstream=upstream, upstream_timeout=0.3)
    started = time.monotonic()
    response = parse_dns_response(dns_udp(service.ports["dns_udp"], "slow-service"))
    elapsed = time.monotonic() - started
    assert elapsed < 3.0, f"answer took {elapsed:.2f}s"
    assert response["a_records"] == ["127.0.0.1"]


def test_real_resolver_path(service):
    """No injection: `localhost` is single-label, so it goes through the container's own
    resolver and comes back truthfully."""
    response = parse_dns_response(dns_udp(service.ports["dns_udp"], "localhost"))
    assert response["a_records"] == ["127.0.0.1"]
    assert len(service.store) == 0


def test_denylisted_name_gets_nxdomain(make_service):
    service = make_service(denylist=("blocked.example",))
    response = parse_dns_response(dns_udp(service.ports["dns_udp"], "blocked.example"))
    assert response["rcode"] == dnswire.RCODE_NXDOMAIN
    assert service.store.wait_for(1, timeout=5)[0].detail["answer"] == "nxdomain"


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
    (record,) = service.store.wait_for(1, timeout=5)
    assert record.detail["malformed"] is True and record.token is None


def test_transaction_id_and_question_are_echoed(service):
    query = build_dns_query("z9x2k1p8.example-collab.net", txid=0xBEEF)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(5)
    try:
        sock.sendto(query, ("127.0.0.1", service.ports["dns_udp"]))
        response = parse_dns_response(sock.recv(4096))
    finally:
        sock.close()
    assert response["txid"] == 0xBEEF
    assert response["question"] == ("z9x2k1p8.example-collab.net", dnswire.TYPE_A)


def test_compressed_question_name_is_parsed():
    header = struct.pack("!6H", 1, 0x0100, 1, 0, 0, 0)
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
