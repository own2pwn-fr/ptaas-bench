"""Follow the web server's own log and turn each line into a record.

The web tier is a file server. Nothing of ours runs inside it, and nothing should: a
file server that answered like an application would be a strange file server, and code
of ours in its request path would add a failure mode to every response on the estate.
What it does instead is what every file server does -- it writes a line per response --
and this reader turns those lines into the same records a service would have reported
for itself.

The line carries what the server observed and nothing a client asserted about itself:
the peer address off the socket, the method, the path after decoding, the status, the
bytes written to the client and the bytes read from it. The last two are what make the
counters in :mod:`site_telemetry.evidence` answerable at all: they are the difference
between a document being asked for and a document being taken.

Following, not sampling: the reader keeps its position, notices the file being replaced
or truncated, and re-opens. A line is processed once.
"""

from __future__ import annotations

import os
import re
import threading
import time
import urllib.parse

from telemetry_agent import describe_param

from . import emit, vhosts

# Fields, in the order the server writes them. The last field is free text, so the split
# is bounded and everything after the ninth separator belongs to it.
FIELDS = ("time", "peer", "method", "path", "query", "status", "sent", "received",
          "host", "agent")
SEPARATOR = "|"

# Path templates, so that a thousand distinct object files do not become a thousand
# routes. The first pattern that matches wins.
TEMPLATES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^/\.git/objects/[0-9a-f]{2}/[0-9a-f]{6,}$"), "/.git/objects/:prefix/:object"),
    (re.compile(r"^/\.git/logs/refs/heads/[^/]+$"), "/.git/logs/refs/heads/:branch"),
    (re.compile(r"^/\.git/refs/heads/[^/]+$"), "/.git/refs/heads/:branch"),
    (re.compile(r"^/\.git/hooks/[^/]+$"), "/.git/hooks/:hook"),
    (re.compile(r"^/careers/portal/\.svn/pristine/[0-9a-f]{2}/[0-9a-f]+\.svn-base$"),
     "/careers/portal/.svn/pristine/:prefix/:object"),
    (re.compile(r"^/careers/portal/\.svn/[^/]+$"), "/careers/portal/.svn/:file"),
    (re.compile(r"^/media/[^/]+$"), "/media/:file"),
    (re.compile(r"^/news/[^/]+\.html$"), "/news/:article"),
    (re.compile(r"^/assets/css/[^/]+$"), "/assets/css/:file"),
    (re.compile(r"^/assets/js/[^/]+$"), "/assets/js/:file"),
    (re.compile(r"^/assets/img/[^/]+$"), "/assets/img/:file"),
    (re.compile(r"^/vendor/[^/]+/[^/]+$"), "/vendor/:package/:file"),
    (re.compile(r"^/handbook/[^/]+$"), "/handbook/:page"),
)


def route_for(path: str, status: int | None) -> str:
    for pattern, template in TEMPLATES:
        if pattern.match(path):
            return template
    if status in (404, 410):
        return "<unmatched>"
    return path


def parse(line: str) -> dict | None:
    """Split one line. Returns None for anything that is not one of ours."""
    parts = line.rstrip("\n").split(SEPARATOR, len(FIELDS) - 1)
    if len(parts) != len(FIELDS):
        return None
    record = dict(zip(FIELDS, parts))
    # A decoded path may itself contain the separator, which pushes the fields along.
    # The two numeric fields are the anchor: if they are not numbers, re-join.
    if not (record["status"][:3].isdigit() and record["sent"].lstrip("-").isdigit()):
        merged = SEPARATOR.join(parts)
        head = merged.split(SEPARATOR)
        if len(head) <= len(FIELDS):
            return None
        extra = len(head) - len(FIELDS)
        record = dict(zip(FIELDS, head[:3] + [SEPARATOR.join(head[3:4 + extra])]
                          + head[4 + extra:]))
        if not record["status"][:3].isdigit():
            return None
    try:
        record["status_code"] = int(record["status"][:3])
    except ValueError:
        record["status_code"] = None
    for name in ("sent", "received"):
        try:
            record[name + "_bytes"] = max(int(record[name]), 0)
        except ValueError:
            record[name + "_bytes"] = 0
    return record


class Follower:
    """One file, followed across truncation and replacement."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.handle = None
        self.identity: tuple[int, int] | None = None
        self.buffer = ""

    def _open(self, *, from_end: bool) -> None:
        try:
            handle = open(self.path, "r", encoding="utf-8", errors="replace")
        except OSError:
            self.handle = None
            return
        stat = os.fstat(handle.fileno())
        self.identity = (stat.st_dev, stat.st_ino)
        if from_end:
            handle.seek(0, os.SEEK_END)
        self.handle = handle
        self.buffer = ""

    def position(self) -> int:
        if self.handle is None:
            try:
                return os.path.getsize(self.path)
            except OSError:
                return 0
        return self.handle.tell()

    def lines(self, *, from_end: bool = False):
        if self.handle is None:
            self._open(from_end=from_end)
            if self.handle is None:
                return
        try:
            stat = os.stat(self.path)
        except OSError:
            stat = None
        if stat is not None:
            if (stat.st_dev, stat.st_ino) != self.identity:
                self.handle.close()
                self._open(from_end=False)
                if self.handle is None:
                    return
            elif stat.st_size < self.handle.tell():
                self.handle.seek(0)     # emptied in place
        while True:
            chunk = self.handle.readline()
            if not chunk:
                return
            if not chunk.endswith("\n"):
                # A partial line: the server is mid-write. Put it back and wait.
                self.handle.seek(self.handle.tell() - len(chunk))
                return
            yield chunk, self.handle.tell()


class AccessLog(threading.Thread):
    """Reads the site's log for as long as the agent runs."""

    daemon = True

    def __init__(self, path: str, counters, interval: float = 0.25,
                 site_domain: str | None = None) -> None:
        super().__init__(name="access-log")
        self.follower = Follower(path)
        self.counters = counters
        self.interval = interval
        self.site_domain = site_domain
        self.stopping = threading.Event()
        self.processed = 0
        # The first open starts at the end of the file: lines already in it describe a
        # previous life of this container and have been reported once already.
        self.opened = False

    def position(self) -> int:
        return self.follower.position()

    def stop(self) -> None:
        self.stopping.set()

    def run(self) -> None:
        while not self.stopping.is_set():
            try:
                for line, offset in self.follower.lines(from_end=not self.opened):
                    self.opened = True
                    self.handle(line, offset)
                self.opened = self.opened or self.follower.handle is not None
            except OSError:
                time.sleep(1.0)
            self.stopping.wait(self.interval)

    def handle(self, line: str, offset: int) -> None:
        record = parse(line)
        if record is None:
            return
        self.processed += 1
        path = record["path"] or "/"
        status = record["status_code"]
        route = route_for(path, status)
        identifier = emit.request_id()
        query = record["query"].lstrip("?")
        params = []
        if query:
            for name, value in urllib.parse.parse_qsl(query, keep_blank_values=True):
                params.append(describe_param(name, "query", value))
        emit.record_request(
            method=record["method"],
            route=route,
            path=path + (("?" + query) if query else ""),
            status=status,
            peer=record["peer"],
            params=params,
            user_agent=record["agent"],
            identifier=identifier,
            # Which of the three sites answered. The same path is a leak on one of them
            # and a correctly refused request on the others, so a record that does not
            # say which one credits all three.
            host=vhosts.resolve(record["host"], self.site_domain),
        )
        self.counters.web_response(
            peer=record["peer"],
            method=record["method"].upper(),
            path=path,
            status=status,
            sent=record["sent_bytes"],
            received=record["received_bytes"],
            host=record["host"],
            route=route,
            identifier=identifier,
            offset=offset,
        )
