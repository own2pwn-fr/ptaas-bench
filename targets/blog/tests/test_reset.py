"""Provisioning is deterministic, idempotent, and reports one line that means something.

The orchestrator records the digest before and after a run and refuses to open the next
one until it is back where it started, so three properties have to hold: the same seed
gives byte-identical content, running provisioning twice changes nothing, and the digest
moves if and only if the content does.
"""

from __future__ import annotations

import io
from contextlib import redirect_stdout


def test_the_same_seed_gives_identical_content():
    from pressroom import seed

    first, second = seed.build(), seed.build()
    assert seed.digest_of(first) == seed.digest_of(second)
    assert [a["email"] for a in first["accounts"]] == [a["email"] for a in second["accounts"]]
    assert [d["_id"] for d in first["drafts"]] == [d["_id"] for d in second["drafts"]]


def test_a_different_seed_shares_no_names(monkeypatch):
    from pressroom import seed
    from pressroom.settings import settings

    settings.cache_clear()
    monkeypatch.setenv("DEPLOY_SEED", "")
    default = seed.build()
    settings.cache_clear()
    monkeypatch.setenv("DEPLOY_SEED", "b3f1c0")
    other = seed.build()
    settings.cache_clear()

    assert seed.digest_of(default) != seed.digest_of(other)
    assert not ({a["email"] for a in default["accounts"]}
                & {a["email"] for a in other["accounts"]})
    assert not ({a["slug"] for a in default["articles"]}
                & {a["slug"] for a in other["articles"]})
    # The key somebody typed into a compose file moves with the seed too.
    from pressroom.settings import _weak_passphrase
    assert _weak_passphrase("") != _weak_passphrase("nothing-like-it")


def test_provisioning_twice_leaves_the_same_digest(stack):
    from pressroom import seed

    first, _ = seed.provision()
    second, counts = seed.provision()
    assert first == second
    assert counts["articles"] > 20 and counts["comments"] > 50


def test_the_digest_moves_when_the_content_does(stack):
    from pressroom import seed
    from pressroom.store import database

    before, _ = seed.provision()
    content = seed.build()
    content["articles"][0]["title"] = "A different headline entirely"
    assert seed.digest_of(content) != before


def test_provisioning_prints_one_line_that_starts_with_the_digest(stack):
    from pressroom import seed

    buffer = io.StringIO()
    with redirect_stdout(buffer):
        assert seed.main([]) == 0
    lines = buffer.getvalue().strip().splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("state ")
    assert "documents=" in lines[0]


def test_provisioning_only_when_empty_leaves_content_alone(stack):
    from pressroom import seed
    from pressroom.store import database

    database()["articles"].update_one({}, {"$set": {"reads": 999999}})
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        assert seed.main(["--if-empty"]) == 0
    assert buffer.getvalue().startswith("state ")
    kept = database()["articles"].find_one({"reads": 999999})
    assert kept is not None, "a restart must not cost the publication its content"


def test_provisioning_clears_the_cache_this_service_owns(stack):
    from pressroom import seed
    from pressroom.store import cache

    cache().setex("session:leftover", 60, "usr-1001")
    cache().sadd("preview:issued", "pv-leftover")
    seed.provision()
    assert not cache().exists("session:leftover")
    assert not cache().sismember("preview:issued", "pv-leftover")
    assert cache().sismember("preview:issued", "pv-0001")


def test_a_reset_puts_back_what_a_replay_changed(stack):
    """The state a run leaves behind must not be inherited by the next tool."""
    from pressroom import seed
    from pressroom.store import cache, database

    before, _ = seed.provision()
    slug = stack.client.get("/api/articles").json()["items"][0]["slug"]
    stack.client.post(f"/api/articles/{slug}/comments",
                      json={"body": "Something a run left behind."})
    stack.client.post("/api/auth/register", json={
        "email": "left.over@mailbox.example", "password": "shingle-foreshore-8812"})
    cache().hincrby(f"feed:reactions:{slug}", "useful", 5)

    after, _ = seed.provision()
    assert after == before
    assert database()["comments"].count_documents({}) == len(stack.content["comments"])
    assert database()["accounts"].count_documents({}) == len(stack.content["accounts"])
    assert not cache().exists(f"feed:reactions:{slug}")
