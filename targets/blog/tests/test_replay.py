"""Run the replay harness's own requests against the in-process service.

``selftest.py`` is the thing that says the corpus is real, so it needs a test of its
own: its request helper is pointed at the in-process service instead of at a container
and every replay is run, then each one's counter is checked for having moved exactly
once and for carrying a peer address.

This does not replace running it against the deployed stack -- it cannot, because the
document store, the cache and the resolver are all substitutes here -- but it does mean
a PoC string cannot rot in the catalog without something failing.
"""

from __future__ import annotations

import importlib.util
import urllib.error
import urllib.request
from email.message import Message
from pathlib import Path

import httpx
import pytest

HERE = Path(__file__).resolve().parents[1]


def load_harness():
    spec = importlib.util.spec_from_file_location("replay_harness", HERE / "selftest.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Answer:
    """What urllib hands back, wrapped around the in-process response."""

    def __init__(self, response) -> None:
        self._response = response
        self.status = response.status_code
        message = Message()
        for name, value in response.headers.multi_items():
            message[name] = value
        self.headers = message

    def read(self) -> bytes:
        return self._response.content

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def route_urllib_to(client, monkeypatch) -> None:
    def urlopen(request, timeout=None):  # noqa: ARG001
        # Stateless on purpose: the harness keeps the only cookie jar, so a jar held
        # by the transport would carry a session into a replay that cleared one.
        client.cookies.clear()
        url = request.full_url
        path = url.split("://", 1)[1].split("/", 1)[1]
        response = client.request(
            request.get_method(), "/" + path, content=request.data,
            headers={k: v for k, v in request.header_items()})
        answer = Answer(response)
        if response.status_code >= 400:
            raise urllib.error.HTTPError(url, response.status_code, "",
                                         answer.headers, None) from None
        return answer

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)


class RecordingClient:
    """Stands in for the outbound client only; the test transport is left alone."""

    def __init__(self, **kwargs) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url, headers=None):
        return httpx.Response(200, content=b'{"service":"pressroom","state":"ok"}',
                              headers={"content-type": "application/json"},
                              request=httpx.Request("GET", url))


@pytest.fixture()
def harness(stack, monkeypatch):
    module = load_harness()
    module.ACCOUNTS = module.load_accounts()
    assert set(module.ACCOUNTS) >= {"user", "other-user", "admin"}
    monkeypatch.setattr(httpx, "Client", RecordingClient)
    route_urllib_to(stack.client, monkeypatch)
    # The harness talks to the service by absolute URL; the router above ignores the
    # authority and keeps the path, so any base works.
    monkeypatch.setattr(module, "BASE", "http://press01:8000")
    return module


def test_the_catalog_and_the_replay_list_agree(harness):
    signals = harness.load_signals()
    assert len(signals) == 26
    assert [ident for ident, _ in harness.POCS] == sorted(signals)


@pytest.mark.parametrize("index", range(26))
def test_each_replay_moves_its_counter_once(stack, harness, index):
    ident, replay = harness.POCS[index]
    signal = harness.load_signals()[ident]
    replay(harness.Session())

    if ident in harness.BY_CORRELATION:
        declared = [link for link in stack.links() if link.get("signal") == signal]
        assert len(declared) >= 1, f"{ident} never declared its destination"
        assert declared[0]["destination_host"]
        return

    raised = stack.signals(signal)
    assert len(raised) == 1, (
        f"{ident} moved {signal} {len(raised)} times; "
        "0 means the replay no longer reaches it, more than 1 means the sink is "
        "counting the payload rather than the effect")
    assert raised[0]["peer_ip"], f"{ident} raised {signal} with no peer address"
    assert raised[0]["attributes"].get("request_id")


def test_no_replay_moves_a_counter_it_does_not_own(stack, harness):
    """Run every replay in order and account for the whole set of counters."""
    expected: dict[str, int] = {}
    for ident, replay in harness.POCS:
        replay(harness.Session())
        if ident not in harness.BY_CORRELATION:
            expected[harness.load_signals()[ident]] = 1
    seen: dict[str, int] = {}
    for record in stack.signals():
        seen[record["signal"]] = seen.get(record["signal"], 0) + 1
    assert seen == expected, {
        "unexpected": sorted(set(seen) - set(expected)),
        "missing": sorted(set(expected) - set(seen)),
        "wrong_count": {k: v for k, v in seen.items() if expected.get(k) != v},
    }
