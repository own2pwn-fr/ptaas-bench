"""A count raised on a worker pool must still describe the request that caused it.

The agent keeps the request's identity in a ``ContextVar``. That survives ``await``,
``asyncio.to_thread`` and the pool the framework runs plain handlers on, but a bare
``ThreadPoolExecutor`` copies nothing: work handed to one starts with an empty context,
and anything it counts arrives with no request id, no peer and no classification of the
traffic that asked for it.

Two of this service's counters are raised inside pool work -- the search matcher and
the scan reader -- so this is the test that says the seam is still there. It is worth
its own file because the failure is silent: the counter still moves, the dashboard
still draws, and only the attribution is gone.
"""

from __future__ import annotations

import base64


def test_a_count_from_the_matching_pool_carries_the_request(stack):
    stack.client.get("/api/search", params={"q": "a" * 1499 + "!"})
    raised = stack.signals("blog.search.pattern.backtrack_budget")
    assert len(raised) == 1
    record = raised[0]
    assert record["peer_ip"], "the count arrived with no peer: the context was lost"
    assert record["attributes"].get("request_id"), "the count is tied to no request"


def test_a_count_from_the_conversion_pool_carries_the_request(stack):
    stack.sign_in(stack.account("author"))
    payload = base64.b64encode(bytes(range(256)) * 4)
    blob = (b"NGP1\nwidth=32\nheight=32\ncurve=__import__('os').system('true')\n"
            + b"data:" + payload)
    stack.client.post("/api/studio/media",
                      files={"file": ("negative-1974.ngp", blob,
                                      "application/octet-stream")})
    raised = stack.signals("blog.media.scan.curve_escape")
    assert len(raised) == 1
    assert raised[0]["peer_ip"], "the count arrived with no peer: the context was lost"
    assert raised[0]["attributes"].get("request_id")


def test_every_count_the_suite_can_raise_carries_a_peer(stack):
    """Whatever else a handler does, a count must name the connection it came from."""
    slug = stack.client.get("/api/articles").json()["items"][0]["slug"]
    stack.client.get(f"/api/articles/{slug}", params={"_trace": "1"})
    stack.client.get("/api/internal/diagnostics")
    stack.client.get("/api/embed/card", params={"title": "{{7*7}}"})
    stack.client.post(f"/api/articles/{slug}/comments",
                      json={"body": "<scr<script>ipt>x</scr<script>ipt>"})
    raised = stack.signals()
    assert len(raised) >= 4
    for record in raised:
        assert record["peer_ip"], f"{record['signal']} arrived with no peer"
        assert record["attributes"].get("request_id"), f"{record['signal']} names no request"
