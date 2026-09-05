"""DNS listener: the catch-all answer, and the narrow paths that are not catch-all."""

from __future__ import annotations

import socket
import struct
import time

from edge_resolver import dnswire

from conftest import (
    ZONE,
    FakeUpstream,
    build_dns_query,
    dns_tcp,
    dns_udp,
    parse_dns_response,
    stub_resolver,
)


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


def _upstream_service(make_service, names=None, hang=False, **overrides):
    """A service whose upstream is a real (fake) DNS server."""
    upstream = FakeUpstream(names or {}, hang=hang)
    resolver = stub_resolver(upstream, timeout=overrides.pop("timeout", 0.15))
    service = make_service(upstream=resolver, **overrides)
    return service, upstream


def test_a_claimed_hostname_is_forwarded_not_sinkholed(make_service):
    """The bring-up case. Applications advertise a hostname generated per deployment;
    answering it with our own address would make every one of them unreachable under the
    name it publishes, and the whole corpus would score zero."""
    service, upstream = _upstream_service(
        make_service, {"www.halyardsupply.net": "10.88.0.20"}
    )
    try:
        response = parse_dns_response(dns_udp(service.ports["dns_udp"], "www.halyardsupply.net"))
        assert response["a_records"] == ["10.88.0.20"]
        assert not response["aa"]  # forwarded, not ours to be authoritative for
        # Application traffic is not recorded; it would bury the requests that matter.
        assert len(service.store) == 0
    finally:
        upstream.close()


def test_plain_service_names_are_forwarded(make_service):
    """Load-bearing for a whole target coming up: its chain resolves its siblings by
    plain service name, and the reporting endpoint is reached the same way."""
    names = {
        "otel-collector": "10.77.0.4",
        "haproxy": "10.88.0.11",
        "varnish": "10.88.0.12",
        "origin": "10.88.0.13",
        "nginx": "10.88.0.14",
    }
    service, upstream = _upstream_service(
        make_service, names, telemetry_url="http://otel-collector:8900"
    )
    try:
        for name, address in names.items():
            response = parse_dns_response(dns_udp(service.ports["dns_udp"], name))
            assert response["a_records"] == [address], name
        assert len(service.store) == 0
    finally:
        upstream.close()


def test_a_name_nobody_claims_is_sinkholed_and_recorded(make_service):
    service, upstream = _upstream_service(make_service, {"www.halyardsupply.net": "10.88.0.20"})
    try:
        response = parse_dns_response(
            dns_udp(service.ports["dns_udp"], "z9x2k1p8.example-collab.net")
        )
        assert response["a_records"] == ["127.0.0.1"]
        (record,) = service.store.wait_for(1, timeout=5)
        assert record.host == "z9x2k1p8.example-collab.net"
        assert "z9x2k1p8.example-collab.net" in upstream.queries  # asked first, every time
    finally:
        upstream.close()


def test_an_upstream_that_hangs_costs_only_the_cap(make_service):
    """A sealed network does not answer NXDOMAIN for an external name, it says nothing.
    That silence must cost one capped wait and then be read as 'unclaimed', because a
    stalled lookup makes a captured callback look like an application error."""
    service, upstream = _upstream_service(make_service, hang=True, timeout=0.15)
    try:
        started = time.monotonic()
        response = parse_dns_response(
            dns_udp(service.ports["dns_udp"], "z9x2k1p8.example-collab.net")
        )
        elapsed = time.monotonic() - started
        assert response["a_records"] == ["127.0.0.1"]
        assert elapsed < 1.0, f"answer took {elapsed:.3f}s"
        assert service.store.wait_for(1, timeout=5)
    finally:
        upstream.close()


def test_the_cap_is_paid_once_per_host(make_service):
    """Repeat lookups of one callback host -- resolution, the connection, a retry -- must
    not each pay the cap."""
    service, upstream = _upstream_service(make_service, hang=True, timeout=0.15)
    try:
        dns_udp(service.ports["dns_udp"], "z9x2k1p8.example-collab.net")
        started = time.monotonic()
        for _ in range(5):
            dns_udp(service.ports["dns_udp"], "z9x2k1p8.example-collab.net")
        elapsed = time.monotonic() - started
        assert elapsed < 0.15, f"five cached lookups took {elapsed:.3f}s"
        assert len(service.store.wait_for(6, timeout=5)) == 6  # all six still recorded
    finally:
        upstream.close()


def test_known_infrastructure_that_upstream_cannot_answer_gets_servfail(make_service):
    """Not our own address: pointing a database client at this process would turn a
    resolver blip into a silent misconnection."""
    service, upstream = _upstream_service(
        make_service,
        {},
        telemetry_url="http://otel-collector:8900",
        internal_names=("db.internal",),
    )
    try:
        for name in ("otel-collector", "db.internal"):
            response = parse_dns_response(dns_udp(service.ports["dns_udp"], name))
            assert response["rcode"] == dnswire.RCODE_SERVFAIL, name
            assert response["answers"] == []
        assert len(service.store) == 0
    finally:
        upstream.close()


def test_no_upstream_configured_sinkholes_everything(service):
    """The degenerate deployment: nothing to ask, so nothing is claimed."""
    response = parse_dns_response(dns_udp(service.ports["dns_udp"], "www.halyardsupply.net"))
    assert response["a_records"] == ["127.0.0.1"]
    assert service.store.wait_for(1, timeout=5)[0].host == "www.halyardsupply.net"


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


def test_a_burst_of_unknown_names_does_not_pile_up(make_service):
    """A tool fuzzing an outbound-fetch parameter mints a new hostname per attempt. The
    cap alone would leave hundreds of lookups in flight, so past the concurrency limit a
    name is simply treated as unclaimed -- for a burst, the fast and correct answer."""
    service, upstream = _upstream_service(make_service, hang=True, timeout=0.15)
    port = service.ports["dns_udp"]
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(5)
    try:
        started = time.monotonic()
        for index in range(40):
            sock.sendto(build_dns_query(f"burst{index}.example-collab.net"), ("127.0.0.1", port))
        for _ in range(40):
            sock.recv(4096)
        elapsed = time.monotonic() - started
        assert len(service.store.wait_for(40, timeout=10)) == 40
        # Forty in flight at once, each capped: they overlap instead of queueing behind
        # one another, so the whole burst costs about one cap, not forty.
        assert elapsed < 2.0, f"the burst serialised on the cap ({elapsed:.2f}s)"
    finally:
        sock.close()
        upstream.close()


def test_an_answer_from_outside_the_deployment_is_not_believed(make_service):
    """The embedded resolver forwards what it cannot answer to the daemon's own DNS, and
    the daemon is not on the sealed network -- so a callback domain can come back with a
    real public address. Forwarding that would lose the capture *and* leave the
    application unable to reach it, since its network has no route out."""
    upstream = FakeUpstream(
        {"oast.example": "93.184.216.34", "www.halyardsupply.net": "10.88.0.20"}
    )
    resolver = stub_resolver(upstream)
    service = make_service(upstream=resolver)
    try:
        public = parse_dns_response(dns_udp(service.ports["dns_udp"], "oast.example"))
        assert public["a_records"] == ["127.0.0.1"]  # sinkholed anyway
        (record,) = service.store.wait_for(1, timeout=5)
        assert record.host == "oast.example"

        # And the deployment's own hostname is still forwarded untouched.
        internal = parse_dns_response(dns_udp(service.ports["dns_udp"], "www.halyardsupply.net"))
        assert internal["a_records"] == ["10.88.0.20"]
    finally:
        upstream.close()
