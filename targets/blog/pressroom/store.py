"""MongoDB and Redis handles.

Both are process-wide and created on first use, because a worker that is never asked
for a document should not open a connection it will not use. The drivers are the
synchronous ones on purpose: every handler in this service is a plain function, which
the framework already runs on its own worker pool, and a second async stack under it
would buy nothing but a second set of timeouts to tune.
"""

from __future__ import annotations

from typing import Any

from .settings import settings

_client: Any = None
_cache: Any = None


def database() -> Any:
    """The publishing database."""
    global _client
    if _client is None:
        from pymongo import MongoClient

        cfg = settings()
        _client = MongoClient(cfg.mongo_url, serverSelectionTimeoutMS=5000, tz_aware=False)
    return _client[settings().mongo_db]


def cache() -> Any:
    """The shared cache: sessions, issued share links, rate counters, feed fragments."""
    global _cache
    if _cache is None:
        import redis

        _cache = redis.Redis.from_url(settings().redis_url, decode_responses=True)
    return _cache


def use(db: Any = None, kv: Any = None) -> None:
    """Point the process at already-built handles.

    The provisioning job and the deployment checks build their own clients and run the
    same code paths as the service; without this they would open a second connection
    pool each and race the service's own.
    """
    global _client, _cache
    if db is not None:
        _client = _Wrapped(db)
    if kv is not None:
        _cache = kv


class _Wrapped:
    """Adapts an already-selected database to the client-shaped lookup above."""

    def __init__(self, db: Any) -> None:
        self._db = db

    def __getitem__(self, _name: str) -> Any:
        return self._db


def reset_handles() -> None:
    global _client, _cache
    _client = None
    _cache = None


def operator_shaped(value: Any) -> bool:
    """True when a value that should have been a scalar arrived as a query fragment.

    A JSON body decoded into a document can put a mapping wherever a string was
    expected, and a mapping whose keys begin with ``$`` is a query operator by the
    time it reaches the driver.
    """
    if isinstance(value, dict):
        return any(str(key).startswith("$") for key in value)
    if isinstance(value, (list, tuple)):
        return any(operator_shaped(item) for item in value)
    return False


def field_paths(document: Any, prefix: str = "") -> set[str]:
    """Every field path a filter document addresses, operators excluded."""
    found: set[str] = set()
    if isinstance(document, dict):
        for key, value in document.items():
            name = str(key)
            if name.startswith("$"):
                found |= field_paths(value, prefix)
            else:
                path = f"{prefix}{name}"
                found.add(path.split(".", 1)[0])
                if isinstance(value, dict) and not any(
                    str(k).startswith("$") for k in value
                ):
                    found |= field_paths(value, path + ".")
    elif isinstance(document, (list, tuple)):
        for item in document:
            found |= field_paths(item, prefix)
    return found
