"""The target address map captured at run open.

Attributing an out-of-band callback means answering "which application made this
connection?", and the answer is an address. The collector sees a correlation hint
arrive over bench-internal (10.77.0.x); the sinkhole sees the callback leave over
bench-public (10.88.0.x). Same container, two addresses, no arithmetic relationship
between them -- so anything that infers one from the other is wrong for every
dual-homed target, which is all of them.

The orchestrator is the only component that can see both, so it captures the map and
hands it over. These tests cover the three ways that goes wrong quietly: a stale map
(addresses are reassigned on restart, and the reset path restarts containers), an
ambiguous map (two applications claiming one address), and a rejected map (the
collector not yet accepting the field, which must not fail the run but must be
recorded).
"""

from __future__ import annotations

from runners._lib.collector import CollectorClient
from runners._lib.config import AppSpec
from runners._lib.internal_http import Response
from runners._lib.topology import (
    address_payload,
    duplicate_addresses,
    inspect_apps,
)

from fakes import FakeDocker, FakeHttp, container_inspect, json_response

EDGE = AppSpec(
    key="edge",
    services=["edge-nginx", "edge-origin"],
    base_url="http://www.halyardsupply.net",
    internal_url="http://nginx",
    reset_service="edge-origin",
)


def edge_docker() -> FakeDocker:
    return FakeDocker(
        containers={
            "cid-edge-nginx": container_inspect(
                "edge-nginx",
                image="nginx:1.21.6-alpine",
                addresses={"bench-public": "10.88.0.3", "bench-internal": "10.77.0.4"},
            ),
            "cid-edge-origin": container_inspect(
                "edge-origin",
                image="platform-edge-edge-origin",
                addresses={"bench-internal": "10.77.0.7"},
            ),
        }
    )


def test_both_addresses_of_a_dual_homed_container_are_captured():
    """The whole point: one container, two networks, two unrelated addresses."""
    topology = inspect_apps(edge_docker(), [EDGE])["edge"]
    nginx = next(s for s in topology.services if s.service == "edge-nginx")
    assert nginx.addresses == {"bench-public": "10.88.0.3", "bench-internal": "10.77.0.4"}
    # And the flat view an address-keyed lookup needs covers every service.
    assert set(topology.addresses) == {"10.88.0.3", "10.77.0.4", "10.77.0.7"}


def test_the_running_image_is_recorded_by_digest_and_content_id_not_by_tag():
    """"The target was at nginx:1.21.6-alpine" is not a re-runnable statement."""
    topology = inspect_apps(edge_docker(), [EDGE])["edge"]
    nginx = next(s for s in topology.services if s.service == "edge-nginx")
    assert nginx.image == "nginx:1.21.6-alpine"
    assert nginx.image_id == "sha256:id-nginx:1.21.6-alpine"
    assert nginx.image_digest == "nginx:1.21.6-alpine@sha256:fake"


def test_a_service_that_is_not_running_is_recorded_rather_than_dropped():
    """A target that was down explains an empty result; a missing line does not."""
    docker = FakeDocker(containers={})
    docker.compose_ps_id = lambda service: None  # type: ignore[assignment]
    topology = inspect_apps(docker, [EDGE])["edge"]
    assert [s.service for s in topology.services] == ["edge-nginx", "edge-origin"]
    assert topology.addresses == []


def test_an_address_claimed_by_two_applications_is_flagged():
    """Then attribution by address is ambiguous and blind findings are unsafe."""
    other = AppSpec(key="blog", services=["blog-api"], base_url="http://blog")
    docker = edge_docker()
    docker.containers["cid-blog-api"] = container_inspect(
        "blog-api", addresses={"bench-internal": "10.77.0.7"}
    )
    topologies = inspect_apps(docker, [EDGE, other])
    assert duplicate_addresses(topologies) == {"10.77.0.7": ["edge", "blog"]}


def test_a_clean_map_has_no_duplicates():
    assert duplicate_addresses(inspect_apps(edge_docker(), [EDGE])) == {}


def test_the_payload_carries_the_flat_list_and_the_per_service_detail():
    payload = address_payload(inspect_apps(edge_docker(), [EDGE]))
    assert set(payload) == {"edge"}
    assert "10.88.0.3" in payload["edge"]["addresses"]
    services = {s["service"]: s for s in payload["edge"]["services"]}
    assert {"network": "bench-public", "ip": "10.88.0.3"} in services["edge-nginx"]["addresses"]


def test_state_digests_travel_with_the_map():
    """The digest either side of the run is what proves the target was seeded."""
    topology = inspect_apps(edge_docker(), [EDGE])["edge"]
    topology.state_digest_before = "sha256:seeded"
    topology.state_digest_after = "sha256:seeded"
    payload = topology.to_dict()
    assert payload["state_digest_before"] == payload["state_digest_after"] == "sha256:seeded"


# -- handing it to the collector -------------------------------------------------


def test_the_map_is_sent_with_the_run():
    sent: list[dict] = []

    def handler(method, url, json_body, headers):
        sent.append(json_body)
        return json_response({"run_id": "r1", "tool": "zap", "active": True})

    http = FakeHttp({"http://127.0.0.1:8900/v1/runs": handler})
    client = CollectorClient(http)
    client.open_run(
        tool="zap", tool_version=None, profile="full", targets=["edge"],
        addresses=address_payload(inspect_apps(edge_docker(), [EDGE])),
    )
    assert "10.88.0.3" in sent[0]["addresses"]["edge"]["addresses"]
    assert client.addresses_accepted is True


def test_a_collector_that_does_not_accept_the_field_yet_does_not_fail_the_run():
    """RunCreate still forbids unknown properties until the field lands.

    Losing the whole run over that would be worse than losing the map, but a run
    where the map was dropped has weaker blind-vulnerability attribution and the
    record has to say so.
    """
    calls: list[dict] = []

    def handler(method, url, json_body, headers):
        calls.append(json_body)
        if "addresses" in json_body:
            return Response(422, '{"detail":"extra fields not permitted"}')
        return json_response({"run_id": "r1", "tool": "zap", "active": True})

    http = FakeHttp({"http://127.0.0.1:8900/v1/runs": handler})
    client = CollectorClient(http)
    run = client.open_run(
        tool="zap", tool_version=None, profile="full", targets=["edge"],
        addresses={"edge": {"addresses": ["10.88.0.3"]}},
    )
    assert run.run_id == "r1"
    assert client.addresses_accepted is False
    assert len(calls) == 2 and "addresses" not in calls[1]


def test_a_404_on_the_control_plane_explains_the_allowlist():
    """It means the caller is outside TELEMETRY_CONTROL_CIDRS, not that a route is missing."""
    http = FakeHttp({"http://127.0.0.1:8900/v1/runs": Response(404, "Not Found")})
    client = CollectorClient(http)
    try:
        client.open_run(tool="zap", tool_version=None, profile="full", targets=[])
    except Exception as exc:  # noqa: BLE001
        assert "TELEMETRY_CONTROL_CIDRS" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("a 404 on run creation must be raised, not ignored")
