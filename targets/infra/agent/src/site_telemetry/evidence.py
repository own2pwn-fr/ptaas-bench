"""The counters this estate keeps about its own leftovers.

Each counter answers one question: did somebody outside actually take a copy of
something that should not be reachable? Not "did somebody ask for it" -- asking is what
every crawler on the internet does all day, and a counter that moved on a request would
be a counter nobody could read. So every condition below is written against what the
server reports having *sent*, or what the datastore reports having *executed*:

* a document counts when the whole of it went out on a GET answered 200, which is
  exactly the case a HEAD, a 304, an aborted transfer and a refused request all fail;
* the generated listing of a directory counts when the response is at least as long as
  the listing itself, i.e. the names left the server;
* the diagnostic method counts when the reply is at least as long as the request that
  provoked it, i.e. the message really was echoed back;
* repository metadata counts only when one client holds both halves of a
  reconstruction -- something that names the tracked files, and the content of one of
  them;
* a datastore counts when it executed a command that returned stored data for a client
  outside the estate, which is why a greeting, a version banner or a lookup that matched
  nothing never moves anything.

Each counter moves at most once between deployments. The deployment routine takes a mark
when it finishes -- a position in the server's log and a moment in time -- and anything
recorded before that mark belongs to the deployment itself and is skipped, which is how
loading the datastores and measuring the listing do not count as somebody reading them.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

MEDIA_LISTING = "infra.media.index.enumerated"
SITE_REPOSITORY = "infra.deploy.repo.object_served"
ENVIRONMENT_FILE = "infra.deploy.env.secret_served"
API_DESCRIPTION = "infra.api.spec.document_served"
DATABASE_DUMP = "infra.archive.dump.transferred"
METHOD_ECHO = "infra.http.method.echo_reflected"
CACHE_READ = "infra.cache.keyspace.read"
RECORDS_READ = "infra.records.collection.read"
SEARCH_READ = "infra.search.index.read"
QUEUE_READ = "infra.queue.keyspace.read"
PORTAL_REPOSITORY = "infra.portal.repo.object_served"
MEDIA_ARCHIVE = "infra.media.archive.transferred"

ARCHIVE_PATH = "/media/wwwroot-preflight-20260712.tar.gz"
LISTING_PATHS = ("/media/", "/media")

Reporter = Callable[..., None]


@dataclass
class _Halves:
    """Which of the two halves of a reconstruction a given client already holds."""

    listing: dict[str, str] = field(default_factory=dict)
    content: dict[str, str] = field(default_factory=dict)


class Counters:
    """Thread-safe: four readers feed it, one deployment routine resets it."""

    def __init__(self, state, sites_root: str, report: Reporter) -> None:
        self.lock = threading.Lock()
        self.report = report
        self.sites_root = sites_root
        self.state = state
        self.log_floor = 0
        self.time_floor = 0.0
        self.raised: set[str] = set()
        self.site = _Halves()
        self.portal = _Halves()
        self._sizes: dict[str, int] = {}

    # ------------------------------------------------------------------ state

    def reload(self, state, *, log_floor: int, at: float | None = None) -> None:
        with self.lock:
            self.state = state
            self.log_floor = log_floor
            self.time_floor = at if at is not None else time.time()
            self.raised.clear()
            self.site = _Halves()
            self.portal = _Halves()
            self._sizes.clear()

    def raise_once(self, name: str, attributes: dict, *, peer: str,
                   route: str | None = None, identifier: str | None = None) -> bool:
        """Report a counter the first time its condition is met, and only then."""
        with self.lock:
            if name in self.raised:
                return False
            self.raised.add(name)
        self.report(name, attributes, peer=peer, route=route, identifier=identifier)
        return True

    # ------------------------------------------------------------------ sizes

    def _root_for(self, host: str) -> str:
        name = (host or "").split(":")[0].lower()
        if name.startswith("static.") or name.startswith("assets."):
            return os.path.join(self.sites_root, "static")
        if name.startswith("docs."):
            return os.path.join(self.sites_root, "docs")
        return os.path.join(self.sites_root, "www")

    def size_on_disk(self, host: str, path: str) -> int | None:
        key = f"{host}|{path}"
        cached = self._sizes.get(key)
        if cached is not None:
            return cached or None
        relative = path.lstrip("/")
        if not relative or ".." in relative:
            return None
        full = os.path.join(self._root_for(host), relative)
        try:
            size = os.path.getsize(full)
        except OSError:
            self._sizes[key] = 0
            return None
        self._sizes[key] = size
        return size

    def transferred(self, host: str, method: str, status: int | None,
                    sent: int, path: str) -> bool:
        """True when the whole document left the server on this response."""
        if method != "GET" or status != 200:
            return False
        size = self.size_on_disk(host, path)
        return size is not None and sent >= size

    # ------------------------------------------------------- the web tier

    def web_response(self, *, peer: str, method: str, path: str, status: int | None,
                     sent: int, received: int, host: str, route: str,
                     identifier: str, offset: int) -> None:
        if offset <= self.log_floor:
            return                      # written before this deployment finished

        if path in LISTING_PATHS and method == "GET" and status == 200 \
                and sent >= self.state.listing_bytes > 0:  # noqa: E129
            self.raise_once(MEDIA_LISTING, {
                "detail": "generated listing of the media directory left the server",
                "bytes": sent,
                "listing_bytes": self.state.listing_bytes,
            }, peer=peer, route=route, identifier=identifier)

        if method == "TRACE" and status == 200 and received > 0 and sent >= received:
            self.raise_once(METHOD_ECHO, {
                "detail": "the request message was echoed back to the client",
                "bytes_in": received,
                "bytes_out": sent,
            }, peer=peer, route=route, identifier=identifier)

        if path == "/.env" and self.transferred(host, method, status, sent, path):
            self.raise_once(ENVIRONMENT_FILE, {
                "detail": "settings file served in full, credentials included",
                "bytes": sent,
            }, peer=peer, route=route, identifier=identifier)

        if path == "/api-docs/openapi.yaml" and self.transferred(host, method, status, sent, path):
            self.raise_once(API_DESCRIPTION, {
                "detail": "interface description served in full",
                "bytes": sent,
            }, peer=peer, route=route, identifier=identifier)

        if path == "/dump.sql.gz" and self.transferred(host, method, status, sent, path):
            self.raise_once(DATABASE_DUMP, {
                "detail": "database archive transferred in full",
                "bytes": sent,
            }, peer=peer, route=route, identifier=identifier)

        if path == ARCHIVE_PATH and self.transferred(host, method, status, sent, path):
            self.raise_once(MEDIA_ARCHIVE, {
                "detail": "archive of the document root transferred in full",
                "bytes": sent,
            }, peer=peer, route=route, identifier=identifier)

        self._repository(peer, method, path, status, sent, host, route, identifier,
                         self.site, self.state.git_listing_urls,
                         self.state.git_content_urls, SITE_REPOSITORY)
        self._repository(peer, method, path, status, sent, host, route, identifier,
                         self.portal, self.state.svn_listing_urls,
                         self.state.svn_content_urls, PORTAL_REPOSITORY)

    def _repository(self, peer, method, path, status, sent, host, route, identifier,
                    halves: _Halves, listing_urls: set[str], content_urls: set[str],
                    name: str) -> None:
        if path not in listing_urls and path not in content_urls:
            return
        if not self.transferred(host, method, status, sent, path):
            return
        with self.lock:
            if path in listing_urls:
                halves.listing.setdefault(peer, path)
            else:
                halves.content.setdefault(peer, path)
            listed = halves.listing.get(peer)
            content = halves.content.get(peer)
        if listed and content:
            self.raise_once(name, {
                "detail": "one client holds both what names the tracked files and the "
                          "content of one of them",
                "listing": listed,
                "content": content,
            }, peer=peer, route=route, identifier=identifier)

    # ------------------------------------------------------- the datastores

    # Commands that hand stored data back to the caller. A greeting, a ping, a
    # description of the server itself and a write are all outside this set: none of
    # them is somebody reading what the store holds.
    READ_COMMANDS = {
        "get", "mget", "getrange", "getdel", "substr",
        "hget", "hmget", "hgetall", "hvals", "hrandfield",
        "lrange", "lindex", "lpos", "rpoplpush",
        "smembers", "srandmember", "sscan", "sinter", "sunion", "sdiff",
        "zrange", "zrangebyscore", "zrangebylex", "zscan", "zscore", "zmscore",
        "hscan", "xrange", "xrevrange", "getex", "dump", "lolwut",
        "object", "sort", "sort_ro", "type",
    }
    ENUMERATION_COMMANDS = {"keys", "scan", "randomkey"}

    def cache_command(self, *, peer: str, instance: str, command: str,
                      arguments: list[str], key_exists: Callable[[str], bool],
                      key_count: Callable[[], int], when: float) -> None:
        if when < self.time_floor:
            return
        name = CACHE_READ if instance == "cache" else QUEUE_READ
        route = f"tcp://{instance}"
        verb = command.lower()
        detail = None
        if verb in self.ENUMERATION_COMMANDS and key_count() > 0:
            detail = "the keyspace was enumerated and had keys to return"
        elif verb in self.READ_COMMANDS and arguments and key_exists(arguments[0]):
            detail = "a stored key was read"
        if detail is None:
            return
        self.raise_once(name, {
            "detail": detail,
            "command": verb,
            "key": arguments[0] if arguments else "",
        }, peer=peer, route=route)

    def records_operation(self, *, peer: str, operation: str, namespace: str,
                          returned: int, detail: str, when: float) -> None:
        if when < self.time_floor:
            return
        if returned < 1:
            return                      # nothing came back, so nothing was disclosed
        self.raise_once(RECORDS_READ, {
            "detail": detail,
            "operation": operation,
            "namespace": namespace,
            "returned": returned,
        }, peer=peer, route="tcp://records")

    # Endpoints that hand back what the cluster holds. The greeting at the root of the
    # port, the cluster health and the node statistics are all outside this set: they
    # describe the server, not its contents.
    SEARCH_DATA_SUFFIXES = ("/_search", "/_msearch", "/_source", "/_mget")
    SEARCH_DOCUMENT = "/_doc/"

    def _search_family(self, path: str) -> tuple[str, int] | None:
        """Which kind of answer this is, and how long an empty one of it would be."""
        bare = path.split("?", 1)[0].rstrip("/")
        if bare.startswith("/_cat/indices") or bare.startswith("/_cat/aliases"):
            # A listing with nothing in it is an empty body.
            return "index-listing", 0
        if bare.endswith(self.SEARCH_DATA_SUFFIXES) or self.SEARCH_DOCUMENT in bare:
            return "documents", self.state.search_empty_bytes
        return None

    def search_response(self, *, peer: str, method: str, path: str, status: int,
                        length: int, route: str, when: float) -> None:
        if when < self.time_floor:
            return
        family = self._search_family(path)
        if family is None or status != 200 or length <= family[1]:
            return
        self.raise_once(SEARCH_READ, {
            "detail": "an answer carrying documents or index rows left the cluster",
            "path": path,
            "kind": family[0],
            "bytes": length,
            "empty_answer_bytes": family[1],
        }, peer=peer, route=route)
