"""Read what the datastores say about the work they are asked to do.

Three stores, three ways of asking them, none of which sits in front of the store or
changes a single byte a client sees:

* the key/value stores publish a stream of the commands they execute, with the address
  of the client that sent each one -- so the agent subscribes and reads it;
* the document store profiles its own operations into a collection, recording the client
  and how many documents came back -- so the agent polls it;
* the search cluster writes a line per request and a line per response into its own log
  file, with the remote address on both -- so the agent follows the file.

All three are the store's own accounting of what it did. Nothing here is a proxy: a
store that were spoken to through something of ours would answer differently under
inspection, and the point of this estate is that it does not.
"""

from __future__ import annotations

import glob
import os
import re
import socket
import threading
import time

from . import emit
from .httplog import Follower
from .seed.stores import KeyValue

# Enough records to describe a scan, not so many that a client hammering a store fills
# the exporter's queue with a thousand identical lines.
REQUEST_RECORD_BUDGET = 500


class KeyValueTap(threading.Thread):
    """Follows one key/value store's command stream."""

    daemon = True

    def __init__(self, name: str, host: str, port: int, counters,
                 route: str | None = None) -> None:
        super().__init__(name=f"tap-{name}")
        self.instance = name
        self.host, self.port = host, port
        self.counters = counters
        self.route = route or f"{host}:{port}"
        self.stopping = threading.Event()
        self.budget = REQUEST_RECORD_BUDGET
        self.seen = 0
        self.lookup: KeyValue | None = None

    def stop(self) -> None:
        self.stopping.set()

    def reset_budget(self) -> None:
        self.budget = REQUEST_RECORD_BUDGET

    # -- helpers used by the counters ---------------------------------------

    def _lookup_client(self) -> KeyValue:
        if self.lookup is None:
            self.lookup = KeyValue(self.host, self.port)
        return self.lookup

    def key_exists(self, key: str) -> bool:
        try:
            return bool(self._lookup_client().command("EXISTS", key))
        except (OSError, RuntimeError, ConnectionError):
            self.lookup = None
            return False

    def key_count(self) -> int:
        try:
            return int(self._lookup_client().command("DBSIZE") or 0)
        except (OSError, RuntimeError, ConnectionError, ValueError):
            self.lookup = None
            return 0

    # -- the stream ----------------------------------------------------------

    def run(self) -> None:
        while not self.stopping.is_set():
            try:
                self.follow()
            except (OSError, RuntimeError, ConnectionError):
                self.stopping.wait(2.0)

    def follow(self) -> None:
        stream = KeyValue(self.host, self.port, timeout=None)
        try:
            stream.send("MONITOR")
            reply = stream.reply()
            if reply != "OK":
                raise RuntimeError(f"the store refused to publish its command stream: {reply}")
            while not self.stopping.is_set():
                line = stream.line().decode("utf-8", "replace")
                self.handle(line)
        finally:
            stream.close()

    # Each executed command arrives as one status reply, so the line carries the marker
    # the protocol puts in front of one. Optional, because the marker is a fact about
    # the wire rather than about the record, and a reader that insists on it would stop
    # reading the moment the store's own framing changed.
    LINE = re.compile(
        r"^\+?(?P<when>\d+\.\d+) \[(?P<db>\d+) (?P<peer>[^\]]+)\] (?P<rest>.*)$")

    def handle(self, line: str) -> None:
        match = self.LINE.match(line)
        if not match:
            return
        peer = match.group("peer").rsplit(":", 1)[0]
        if peer.startswith("unix"):
            return                          # the store's own housekeeping
        try:
            when = float(match.group("when"))
        except ValueError:
            when = time.time()
        arguments = unquote_arguments(match.group("rest"))
        if not arguments:
            return
        command, rest = arguments[0], arguments[1:]
        self.seen += 1
        if self.budget > 0:
            self.budget -= 1
            emit.record_request(
                method="GET",
                route=self.route,
                path=f"{self.route}/{command.lower()}",
                status=200,
                peer=peer,
                user_agent="",
            )
        self.counters.cache_command(
            peer=peer,
            instance=self.instance,
            command=command,
            arguments=rest,
            key_exists=self.key_exists,
            key_count=self.key_count,
            when=when,
        )


def unquote_arguments(rest: str) -> list[str]:
    """Split the quoted arguments the command stream writes."""
    out: list[str] = []
    index, length = 0, len(rest)
    while index < length:
        while index < length and rest[index] == " ":
            index += 1
        if index >= length:
            break
        if rest[index] != '"':
            end = rest.find(" ", index)
            end = length if end < 0 else end
            out.append(rest[index:end])
            index = end
            continue
        index += 1
        buffer = []
        while index < length and rest[index] != '"':
            char = rest[index]
            if char == "\\" and index + 1 < length:
                following = rest[index + 1]
                if following == "x" and index + 3 < length:
                    try:
                        buffer.append(chr(int(rest[index + 2:index + 4], 16)))
                        index += 4
                        continue
                    except ValueError:
                        pass
                buffer.append({"n": "\n", "r": "\r", "t": "\t"}.get(following, following))
                index += 2
                continue
            buffer.append(char)
            index += 1
        index += 1
        out.append("".join(buffer))
    return out


class RecordsTap(threading.Thread):
    """Polls the document store's own operation profile."""

    daemon = True
    APPLICATION = "ops-metrics"

    def __init__(self, host: str, port: int, database: str, counters,
                 interval: float = 0.25, label: str | None = None) -> None:
        super().__init__(name="tap-records")
        self.host, self.port, self.database = host, port, database
        self.route = label or f"{host}:{port}"
        self.counters = counters
        self.interval = interval
        self.stopping = threading.Event()
        self.cursor = None
        self.budget = REQUEST_RECORD_BUDGET
        self.client = None

    def stop(self) -> None:
        self.stopping.set()

    def reset_budget(self) -> None:
        self.budget = REQUEST_RECORD_BUDGET

    def connect(self):
        from pymongo import MongoClient

        if self.client is None:
            self.client = MongoClient(
                f"mongodb://{self.host}:{self.port}/",
                appname=self.APPLICATION,
                serverSelectionTimeoutMS=5000,
            )
        return self.client

    def run(self) -> None:
        while not self.stopping.is_set():
            try:
                self.poll()
            except Exception:               # the store is down or restarting
                self.client = None
                self.stopping.wait(2.0)
                continue
            self.stopping.wait(self.interval)

    def poll(self) -> None:
        client = self.connect()
        names = [self.database, "admin"]
        try:
            names = [name for name in client.list_database_names()
                     if name not in ("config", "local")] or names
        except Exception:
            pass
        for name in names:
            profile = client[name]["system.profile"]
            query = {} if self.cursor is None else {"ts": {"$gt": self.cursor}}
            for entry in profile.find(query).sort("ts", 1).limit(200):
                self.handle(name, entry)

    def handle(self, database: str, entry: dict) -> None:
        when = entry.get("ts")
        if when is not None and (self.cursor is None or when > self.cursor):
            self.cursor = when
        if entry.get("appName") == self.APPLICATION:
            return                          # our own polling
        namespace = entry.get("ns", "")
        if namespace.endswith("system.profile"):
            return
        peer = str(entry.get("client") or "").rsplit(":", 1)[0]
        if not peer:
            return
        moment = when.timestamp() if hasattr(when, "timestamp") else time.time()
        command = entry.get("command") or {}
        operation = entry.get("op", "")
        returned = int(entry.get("nreturned") or 0)
        detail = "documents were returned to the caller"
        if any(key in command for key in ("listDatabases", "listCollections", "listIndexes")):
            returned = max(returned, 1)
            detail = "the store enumerated what it holds for the caller"
        if self.budget > 0:
            self.budget -= 1
            emit.record_request(
                method="GET",
                route=self.route,
                path=f"{self.route}/{namespace or database}",
                status=200,
                peer=peer,
            )
        self.counters.records_operation(
            peer=peer,
            operation=operation or next(iter(command), ""),
            namespace=namespace or database,
            returned=returned,
            detail=detail,
            when=moment,
        )


class SearchTap(threading.Thread):
    """Follows the search cluster's request log."""

    daemon = True

    # The cluster writes, in bracketed fields: the request number, the correlation
    # header the caller may have sent (`null` when it did not), then the method and the
    # target on the way in, and the outcome, the media type and the length on the way
    # out. The outcome is written by name -- `OK`, `NOT_FOUND` -- rather than as a
    # number, and older releases wrote the number, so both spellings are read.
    RECEIVED = re.compile(
        r"\[(?P<identifier>\d+)\]\[(?P<correlation>[^\]]*)\]\[(?P<method>[A-Z]+)\]"
        r"\[(?P<path>[^\]]+)\] received request from \[(?P<channel>.*)\]")
    SENT = re.compile(
        r"\[(?P<identifier>\d+)\]\[(?P<correlation>[^\]]*)\]\[(?P<status>[A-Z0-9_]+)\]"
        r"\[(?P<type>[^\]]*)\]\[(?P<length>\d+)\] sent response to \[(?P<channel>.*?)\]")
    ADDRESS = re.compile(r"remoteAddress=/?(?P<host>[0-9a-fA-F:.]+):(?P<port>\d+)")

    # The outcome names the cluster writes, and what each of them is on the wire. A name
    # this table does not hold resolves to nothing rather than to a guess: an outcome
    # nobody recognises must not be read as a successful answer.
    STATUS_CODES = {
        "CONTINUE": 100, "SWITCHING_PROTOCOLS": 101,
        "OK": 200, "CREATED": 201, "ACCEPTED": 202, "NON_AUTHORITATIVE_INFORMATION": 203,
        "NO_CONTENT": 204, "RESET_CONTENT": 205, "PARTIAL_CONTENT": 206,
        "MULTI_STATUS": 207,
        "MULTIPLE_CHOICES": 300, "MOVED_PERMANENTLY": 301, "FOUND": 302,
        "SEE_OTHER": 303, "NOT_MODIFIED": 304, "USE_PROXY": 305,
        "TEMPORARY_REDIRECT": 307,
        "BAD_REQUEST": 400, "UNAUTHORIZED": 401, "PAYMENT_REQUIRED": 402,
        "FORBIDDEN": 403, "NOT_FOUND": 404, "METHOD_NOT_ALLOWED": 405,
        "NOT_ACCEPTABLE": 406, "PROXY_AUTHENTICATION": 407, "REQUEST_TIMEOUT": 408,
        "CONFLICT": 409, "GONE": 410, "LENGTH_REQUIRED": 411,
        "PRECONDITION_FAILED": 412, "REQUEST_ENTITY_TOO_LARGE": 413,
        "REQUEST_URI_TOO_LONG": 414, "UNSUPPORTED_MEDIA_TYPE": 415,
        "REQUESTED_RANGE_NOT_SATISFIED": 416, "EXPECTATION_FAILED": 417,
        "UNPROCESSABLE_ENTITY": 422, "LOCKED": 423, "FAILED_DEPENDENCY": 424,
        "TOO_MANY_REQUESTS": 429,
        "INTERNAL_SERVER_ERROR": 500, "NOT_IMPLEMENTED": 501, "BAD_GATEWAY": 502,
        "SERVICE_UNAVAILABLE": 503, "GATEWAY_TIMEOUT": 504,
        "HTTP_VERSION_NOT_SUPPORTED": 505, "INSUFFICIENT_STORAGE": 507,
    }

    @classmethod
    def status_code(cls, written: str) -> int:
        if written.isdigit():
            return int(written)
        return cls.STATUS_CODES.get(written, 0)

    TEMPLATES = (
        (re.compile(r"^/_cat/(?P<what>[a-z]+)"), "/_cat/:what"),
        (re.compile(r"^/_search"), "/_search"),
        (re.compile(r"^/[^/_][^/]*/_search"), "/:index/_search"),
        (re.compile(r"^/[^/_][^/]*/_doc/"), "/:index/_doc/:id"),
        (re.compile(r"^/[^/_][^/]*/_count"), "/:index/_count"),
        (re.compile(r"^/[^/_][^/]*/?$"), "/:index"),
    )

    def __init__(self, log_dir: str, counters, interval: float = 0.25) -> None:
        super().__init__(name="tap-search")
        self.log_dir = log_dir
        self.counters = counters
        self.interval = interval
        self.stopping = threading.Event()
        self.followers: dict[str, Follower] = {}
        self.pending: dict[str, tuple[str, str, str, float]] = {}
        self.budget = REQUEST_RECORD_BUDGET
        self.matched = 0

    def stop(self) -> None:
        self.stopping.set()

    def reset_budget(self) -> None:
        self.budget = REQUEST_RECORD_BUDGET

    def interesting(self, path: str) -> bool:
        name = os.path.basename(path)
        return (name.endswith(".log")
                and not any(word in name for word in
                            ("deprecation", "slowlog", "audit", "gc", "index_search")))

    def run(self) -> None:
        while not self.stopping.is_set():
            try:
                for path in sorted(glob.glob(os.path.join(self.log_dir, "*.log"))):
                    if not self.interesting(path):
                        continue
                    follower = self.followers.get(path)
                    if follower is None:
                        follower = self.followers[path] = Follower(path)
                    for line, _offset in follower.lines():
                        self.handle(line)
            except OSError:
                pass
            self.expire()
            self.stopping.wait(self.interval)

    def expire(self, older_than: float = 120.0) -> None:
        cutoff = time.time() - older_than
        for identifier in [key for key, value in self.pending.items() if value[3] < cutoff]:
            self.pending.pop(identifier, None)

    def route_for(self, path: str) -> str:
        bare = path.split("?", 1)[0]
        for pattern, template in self.TEMPLATES:
            if pattern.match(bare):
                return template
        return bare

    def handle(self, line: str) -> None:
        received = self.RECEIVED.search(line)
        if received:
            address = self.ADDRESS.search(received.group("channel"))
            if not address:
                return
            self.pending[received.group("identifier")] = (
                received.group("method"), received.group("path"),
                address.group("host"), time.time())
            return
        sent = self.SENT.search(line)
        if not sent:
            return
        entry = self.pending.pop(sent.group("identifier"), None)
        if entry is None:
            return
        method, path, peer, _when = entry
        self.matched += 1
        route = self.route_for(path)
        status = self.status_code(sent.group("status"))
        if self.budget > 0 and route.strip("/"):
            self.budget -= 1
            emit.record_request(
                method=method,
                route=route,
                path=path,
                status=status,
                peer=peer,
            )
        self.counters.search_response(
            peer=peer,
            method=method,
            path=path,
            status=status,
            length=int(sent.group("length")),
            route=route,
            when=time.time(),
        )


def wait_for_port(host: str, port: int, seconds: float) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=2):
                return True
        except OSError:
            time.sleep(0.5)
    return False


def diagnostics(taps) -> str:
    """A one-line description of what each reader has managed to read so far."""
    parts = []
    for tap in taps:
        if isinstance(tap, KeyValueTap):
            parts.append(f"{tap.instance}={tap.seen}")
        elif isinstance(tap, RecordsTap):
            parts.append(f"records={'connected' if tap.client else 'down'}")
        elif isinstance(tap, SearchTap):
            parts.append(f"search={tap.matched}")
    return " ".join(parts) or "no readers"


__all__ = ["KeyValueTap", "RecordsTap", "SearchTap", "unquote_arguments",
           "wait_for_port", "diagnostics"]
