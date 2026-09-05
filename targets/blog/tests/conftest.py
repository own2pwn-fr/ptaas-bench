"""Local doubles for the two datastores, so the service can be exercised in one process.

The deployed service talks to MongoDB and Redis. These tests talk to in-process
substitutes, which is the only way to run the whole request path -- middleware,
routing, validation, counters -- without a compose stack.

One thing the substitute does not implement is the server-side predicate
(``$where``), and the archive and the readership reports are built on it. The shim
below supplies it with the loose comparison semantics the real server's JavaScript
uses, so that ``this.reads > '500'`` compares numbers and ``'1'=='1'`` compares
strings, and the two filter code paths can be exercised for real. It lives here, in
the test tree, and is never shipped.
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

NUMERIC = re.compile(r"^-?\d+(?:\.\d+)?$")


class Loose:
    """A value compared the way the database's own expression language compares it."""

    __slots__ = ("raw",)

    def __init__(self, raw: Any) -> None:
        self.raw = raw

    def _pair(self, other: Any) -> tuple[Any, Any]:
        mine = self.raw
        theirs = other.raw if isinstance(other, Loose) else other
        if isinstance(mine, bool) or isinstance(theirs, bool):
            return bool(mine), bool(theirs)
        if _numeric(mine) and _numeric(theirs):
            return float(mine), float(theirs)
        return ("" if mine is None else str(mine)), ("" if theirs is None else str(theirs))

    def __eq__(self, other: Any) -> bool:
        left, right = self._pair(other)
        return left == right

    def __ne__(self, other: Any) -> bool:
        return not self.__eq__(other)

    def __lt__(self, other: Any) -> bool:
        left, right = self._pair(other)
        return left < right

    def __le__(self, other: Any) -> bool:
        left, right = self._pair(other)
        return left <= right

    def __gt__(self, other: Any) -> bool:
        left, right = self._pair(other)
        return left > right

    def __ge__(self, other: Any) -> bool:
        left, right = self._pair(other)
        return left >= right

    def __bool__(self) -> bool:
        return bool(self.raw)

    def __repr__(self) -> str:
        return f"Loose({self.raw!r})"


def _numeric(value: Any) -> bool:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return True
    return isinstance(value, str) and bool(NUMERIC.match(value.strip()))


_FIELD = re.compile(r"\bthis\.([A-Za-z_][A-Za-z0-9_]*)")


def js_to_python(clause: str) -> str:
    body = _FIELD.sub(lambda m: f"V(_d.get({m.group(1)!r}))", clause)
    body = body.replace("||", " or ").replace("&&", " and ")
    body = re.sub(r"\btrue\b", "True", body)
    body = re.sub(r"\bfalse\b", "False", body)
    body = re.sub(r"\bnull\b", "None", body)
    body = re.sub(r"\bsleep\s*\(", "SLEEP(", body)
    return body


def evaluate(clause: str, document: dict[str, Any]) -> bool:
    try:
        return bool(eval(js_to_python(clause),  # noqa: S307 - a test double for the server
                         {"V": Loose, "SLEEP": lambda ms: time.sleep(min(ms, 4000) / 1000)},
                         {"_d": document}))
    except Exception:  # noqa: BLE001 - the server answers an unusable predicate with nothing
        return False


def install_predicate_support(module: Any) -> None:
    """Teach the substitute to answer a ``$where`` filter."""
    original = module.Collection.find

    def find(self, filter=None, *args, **kwargs):  # noqa: A002
        if isinstance(filter, dict) and "$where" in filter:
            clause = filter["$where"]
            rest = {k: v for k, v in filter.items() if k != "$where"}
            matched = [document["_id"] for document in original(self, rest)
                       if evaluate(clause, document)]
            return original(self, {"_id": {"$in": matched}}, *args, **kwargs)
        return original(self, filter, *args, **kwargs)

    module.Collection.find = find


@pytest.fixture(scope="session", autouse=True)
def _patched_datastore():
    import mongomock

    install_predicate_support(mongomock.collection)
    yield


@pytest.fixture()
def stack(tmp_path, monkeypatch):
    """A provisioned service, its two substitutes, and everything it counted."""
    import fakeredis
    import mongomock

    monkeypatch.setenv("MEDIA_ROOT", str(tmp_path / "media"))
    monkeypatch.setenv("DEPLOY_SEED", "")
    monkeypatch.setenv("TELEMETRY_ENABLED", "0")

    from pressroom import store
    from pressroom.settings import settings

    settings.cache_clear()
    store.reset_handles()
    store.use(db=mongomock.MongoClient()["pressroom"], kv=fakeredis.FakeStrictRedis(decode_responses=True))

    from pressroom import seed
    from pressroom.observability import telemetry

    content = seed.build()
    seed.provision(content=content)

    records: list[dict[str, Any]] = []
    monkeypatch.setattr(telemetry, "emit", records.append)
    monkeypatch.setattr(telemetry, "_dispatch_link", records.append)

    from fastapi.testclient import TestClient
    from pressroom.main import app

    # The middleware reads the socket peer off the scope; the test transport reports
    # the loopback, which is neither the operations range nor a generated-traffic one.
    with TestClient(app) as client:
        yield Stack(client=client, records=records, accounts=content["accounts"],
                    content=content)
    settings.cache_clear()
    store.reset_handles()


class Stack:
    def __init__(self, client, records, accounts, content) -> None:
        self.client = client
        self.records = records
        self.accounts = accounts
        self.content = content

    def signals(self, name: str | None = None) -> list[dict[str, Any]]:
        # A dependency link names a signal too, but it is a link, not a count: the
        # sinkhole raises the count for those when it sees the callback.
        found = [r for r in self.records
                 if r.get("type") == "signal" and r.get("signal")]
        return [r for r in found if name is None or r["signal"] == name]

    def links(self, name: str | None = None) -> list[dict[str, Any]]:
        found = [r for r in self.records if r.get("destination_host")]
        return [r for r in found if name is None or r.get("signal") == name]

    def counted(self, name: str) -> int:
        return len(self.signals(name))

    def account(self, role: str, index: int = 0) -> dict[str, Any]:
        matching = [a for a in self.accounts if a["role"] == role]
        return matching[index]

    def sign_in(self, account: dict[str, Any]) -> None:
        response = self.client.post("/api/auth/session", json={
            "email": account["email"], "password": account["provisioned_passphrase"]})
        assert response.status_code == 200, response.text

    def sign_out(self) -> None:
        self.client.cookies.clear()
