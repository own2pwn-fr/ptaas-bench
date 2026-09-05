"""Load the datastores with the working set the estate normally holds.

Three kinds of store are in use and all three are loaded from here so that a rebuilt
deployment holds exactly what the previous one did: the page cache and the maintenance
queue (key/value), the records database (documents), and the search cluster (indices).
The two stores that carry a password are loaded through it like any other client.

Every routine returns a manifest -- what it wrote, and how much of it -- which the setup
routine folds into the state digest it prints.
"""

from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request


# ---------------------------------------------------------------------------
# key/value: a small client, because the deployment host carries no client library
# ---------------------------------------------------------------------------

class KeyValue:
    """Just enough of the protocol to load, read back and follow a store."""

    def __init__(self, host: str, port: int, password: str | None = None,
                 timeout: float = 5.0) -> None:
        self.host, self.port, self.password, self.timeout = host, port, password, timeout
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.buffer = b""
        if password:
            self.command("AUTH", password)

    # -- wire ---------------------------------------------------------------

    def send(self, *args: object) -> None:
        out = f"*{len(args)}\r\n".encode()
        for arg in args:
            raw = arg if isinstance(arg, bytes) else str(arg).encode()
            out += b"$%d\r\n%s\r\n" % (len(raw), raw)
        self.sock.sendall(out)

    def line(self) -> bytes:
        while b"\r\n" not in self.buffer:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("store closed the connection")
            self.buffer += chunk
        line, _, rest = self.buffer.partition(b"\r\n")
        self.buffer = rest
        return line

    def _exact(self, count: int) -> bytes:
        while len(self.buffer) < count:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("store closed the connection")
            self.buffer += chunk
        out, self.buffer = self.buffer[:count], self.buffer[count:]
        return out

    def reply(self):
        head = self.line()
        kind, rest = head[:1], head[1:]
        if kind == b"+":
            return rest.decode()
        if kind == b"-":
            raise RuntimeError(rest.decode())
        if kind == b":":
            return int(rest)
        if kind == b"$":
            length = int(rest)
            if length < 0:
                return None
            payload = self._exact(length + 2)[:-2]
            return payload
        if kind == b"*":
            count = int(rest)
            if count < 0:
                return None
            return [self.reply() for _ in range(count)]
        raise RuntimeError(f"unexpected reply {head!r}")

    def command(self, *args: object):
        self.send(*args)
        return self.reply()

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


def wait_for(host: str, port: int, seconds: float = 90.0) -> None:
    deadline = time.time() + seconds
    last: Exception | None = None
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=3):
                return
        except OSError as error:  # not up yet
            last = error
            time.sleep(0.5)
    raise TimeoutError(f"{host}:{port} did not accept connections: {last}")


def load_cache(ctx, host: str, port: int, password: str | None = None) -> dict:
    """The page cache in front of the site, plus the sessions it holds."""
    wait_for(host, port)
    store = KeyValue(host, port, password)
    try:
        store.command("FLUSHALL")
        written = 0
        for slug in ("index", "about", "services", "capabilities", "projects",
                     "contact", "careers", "news/index"):
            body = (f"<!-- rendered page: {slug} -->"
                    f"<h1>{slug.replace('/', ' ').title()}</h1>").encode()
            store.command("SET", f"nlf_cache:page:{slug}", body, "EX", 86400)
            written += 1
        for index in range(12):
            person = ctx.person(index)
            token = ctx.token(f"cache/session/{index}", 40)
            store.command(
                "SET",
                f"nlf_cache:session:{token}",
                json.dumps({
                    "user": person.email,
                    "name": person.name,
                    "role": person.role,
                    "ip": f"10.20.{index % 6}.{40 + index}",
                    "csrf": ctx.token(f"cache/csrf/{index}", 32),
                }),
                "EX", 7200,
            )
            written += 1
        store.command("HSET", "nlf_cache:rates",
                      "plate", str(ctx.number("rate/plate", 800, 1400)),
                      "handrail", str(ctx.number("rate/handrail", 30, 90)),
                      "erection", str(ctx.number("rate/erection", 400, 900)))
        store.command("SET", "nlf_cache:sitemap:generated", "2026-07-12T02:14:07Z")
        written += 2
        return {"host": host, "port": port, "keys": written,
                "dbsize": store.command("DBSIZE")}
    finally:
        store.close()


def load_queue(ctx, host: str, port: int, password: str | None = None) -> dict:
    """The maintenance queue: pending jobs and the locks that keep them apart."""
    wait_for(host, port)
    store = KeyValue(host, port, password)
    try:
        store.command("FLUSHALL")
        jobs = [
            {"job": "sitemap:rebuild", "attempts": 0,
             "payload": {"host": ctx.www_host}},
            {"job": "records:export", "attempts": 1,
             "payload": {"target": f"sftp://exports.mgmt.{ctx.domain}/records",
                         "token": ctx.token("ops/export-token", 36)}},
            {"job": "media:reindex", "attempts": 0,
             "payload": {"root": "/srv/sites/www/media"}},
            {"job": "mail:digest", "attempts": 0,
             "payload": {"to": ctx.person(2).email}},
        ]
        for job in jobs:
            store.command("RPUSH", "nlf_ops:queue:maintenance", json.dumps(job))
        store.command("SET", "nlf_ops:lock:maintenance", ctx.token("ops/lock", 24), "EX", 300)
        store.command("HSET", "nlf_ops:last-run",
                      "sitemap:rebuild", "2026-07-12T02:14:07Z",
                      "records:export", "2026-07-11T23:00:04Z",
                      "media:reindex", "2026-07-09T02:11:55Z")
        return {"host": host, "port": port, "jobs": len(jobs),
                "dbsize": store.command("DBSIZE")}
    finally:
        store.close()


def load_sessions(ctx, host: str, port: int, password: str) -> dict:
    """The session store. It carries a password, so this is an authenticated load."""
    wait_for(host, port)
    store = KeyValue(host, port, password)
    try:
        store.command("FLUSHALL")
        for index in range(6):
            person = ctx.person(index)
            store.command("SET", f"nlf_sessions:{ctx.token(f'sessions/{index}', 40)}",
                          json.dumps({"user": person.email, "expires": 7200}), "EX", 7200)
        return {"host": host, "port": port, "dbsize": store.command("DBSIZE")}
    finally:
        store.close()


# ---------------------------------------------------------------------------
# documents
# ---------------------------------------------------------------------------

def load_records(ctx, host: str, port: int, database: str) -> dict:
    """Enquiries, delivery notes and the works diary."""
    from pymongo import MongoClient

    wait_for(host, port)
    client = MongoClient(f"mongodb://{host}:{port}/", serverSelectionTimeoutMS=20000)
    try:
        client.admin.command("ping")
        db = client[database]
        for name in ("enquiries", "delivery_notes", "diary"):
            db[name].drop()

        firms = ("Harker Plant", "Deeside Marine", "Ravensworth Civils", "Pellet & Sons",
                 "Coldstream Rail", "Bexley Aggregates", "Trent Valley Cranes",
                 "Kirkgate Developments", "Ossett Precast", "Marston Wharf",
                 "Ellerby Groundworks", "Sandal Bridge Works", "Nafferton Steel",
                 "Brough Marine", "Hedon Road Storage", "Paull Quay Services")
        enquiries = []
        for index, firm in enumerate(firms, start=1):
            person = ctx.person(index)
            enquiries.append({
                "_id": f"ENQ-{ctx.year}-{index:04d}",
                "company": firm,
                "contact": person.name,
                "email": f"{person.first.lower()}@{firm.split()[0].lower()}.co.uk",
                "telephone": f"01{ctx.number(f'records/tel/{index}', 100000000, 999999999)}",
                "subject": ctx.pick(f"records/subject/{index}",
                                    ("handrail", "walkway", "access platform",
                                     "stair flight", "gantry", "pipe support")),
                "tonnes": ctx.number(f"records/tonnes/{index}", 2, 40),
                "received_at": f"2026-0{index % 6 + 1}-{index % 27 + 1:02d}T09:{index % 60:02d}:00Z",
                "status": ctx.pick(f"records/status/{index}",
                                   ("new", "quoted", "won", "lost")),
            })
        db.enquiries.insert_many(enquiries)

        notes = []
        for index in range(1, 25):
            notes.append({
                "_id": f"DN-{ctx.year}-{index:05d}",
                "order": f"WO-{ctx.year}-{ctx.number(f'records/order/{index}', 1000, 9999)}",
                "customer": ctx.pick(f"records/customer/{index}", firms),
                "items": [
                    {"description": "Handrail, standard section",
                     "quantity": ctx.number(f"records/qty/{index}", 4, 60), "unit": "m"},
                    {"description": "Walkway grating, galvanised",
                     "quantity": ctx.number(f"records/qty2/{index}", 2, 30), "unit": "m2"},
                ],
                "dispatched_at": f"2026-0{index % 6 + 1}-{index % 27 + 1:02d}T14:00:00Z",
                "driver": ctx.person(index).name,
                "signed_by": ctx.person(index + 3).name,
            })
        db.delivery_notes.insert_many(notes)

        db.diary.insert_many([
            {"_id": f"DAY-2026-07-{day:02d}",
             "shift": "days",
             "supervisor": ctx.person(day).name,
             "bays": ctx.number(f"records/bays/{day}", 2, 6),
             "note": ctx.pick(f"records/note/{day}",
                              ("Crane inspection due.", "Coating booth at capacity.",
                               "Two agency welders on shift.", "Plate delivery late."))}
            for day in range(1, 15)
        ])

        for name in ("enquiries", "delivery_notes", "diary"):
            db[name].create_index("_id")
        # Operation profiling, so the store's own log records what was run against it.
        for name in (database, "admin"):
            try:
                client[name].command({"profile": 2, "slowms": 0})
            except Exception:  # a database that refuses profiling must not stop the load
                pass
        counts = {name: db[name].count_documents({})
                  for name in ("enquiries", "delivery_notes", "diary")}
        return {"host": host, "port": port, "database": database, "counts": counts}
    finally:
        client.close()


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------

def _search_request(base: str, method: str, path: str, body: bytes | None = None,
                    content_type: str = "application/json", timeout: float = 20.0):
    request = urllib.request.Request(base + path, data=body, method=method)
    if body is not None:
        request.add_header("Content-Type", content_type)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status, response.read()


def wait_for_search(base: str, seconds: float = 240.0) -> None:
    deadline = time.time() + seconds
    last: Exception | None = None
    while time.time() < deadline:
        try:
            status, _ = _search_request(base, "GET", "/_cluster/health?wait_for_status=yellow&timeout=5s")
            if status == 200:
                return
        except (urllib.error.URLError, OSError) as error:
            last = error
        time.sleep(1.0)
    raise TimeoutError(f"{base} did not become available: {last}")


def load_search(ctx, base: str, index: str, notes_index: str) -> dict:
    """Enquiries and delivery notes, indexed for the works system's search box."""
    wait_for_search(base)
    for name in (index, notes_index):
        try:
            _search_request(base, "DELETE", f"/{name}")
        except urllib.error.HTTPError as error:
            if error.code not in (404,):
                raise
    settings = json.dumps({
        "settings": {"number_of_shards": 1, "number_of_replicas": 0},
        "mappings": {"properties": {
            "company": {"type": "text"},
            "contact": {"type": "text"},
            "email": {"type": "keyword"},
            "subject": {"type": "text"},
            "received_at": {"type": "date"},
        }},
    }).encode()
    for name in (index, notes_index):
        _search_request(base, "PUT", f"/{name}", settings)

    firms = ("Harker Plant", "Deeside Marine", "Ravensworth Civils", "Pellet & Sons",
             "Coldstream Rail", "Bexley Aggregates", "Trent Valley Cranes",
             "Kirkgate Developments", "Ossett Precast", "Marston Wharf")
    lines = []
    for number, firm in enumerate(firms, start=1):
        person = ctx.person(number)
        lines.append(json.dumps({"index": {"_index": index, "_id": f"ENQ-{ctx.year}-{number:04d}"}}))
        lines.append(json.dumps({
            "company": firm,
            "contact": person.name,
            "email": f"{person.first.lower()}@{firm.split()[0].lower()}.co.uk",
            "telephone": f"01{ctx.number(f'search/tel/{number}', 100000000, 999999999)}",
            "subject": ctx.pick(f"search/subject/{number}",
                                ("handrail", "walkway", "access platform", "gantry")),
            "received_at": f"2026-0{number % 6 + 1}-{number % 27 + 1:02d}T09:00:00Z",
        }))
    for number in range(1, 21):
        lines.append(json.dumps({"index": {"_index": notes_index,
                                           "_id": f"DN-{ctx.year}-{number:05d}"}}))
        lines.append(json.dumps({
            "order": f"WO-{ctx.year}-{ctx.number(f'search/order/{number}', 1000, 9999)}",
            "customer": ctx.pick(f"search/customer/{number}", firms),
            "driver": ctx.person(number).name,
            "dispatched_at": f"2026-0{number % 6 + 1}-{number % 27 + 1:02d}T14:00:00Z",
        }))
    body = ("\n".join(lines) + "\n").encode()
    _search_request(base, "POST", "/_bulk", body, content_type="application/x-ndjson")
    _search_request(base, "POST", f"/{index},{notes_index}/_refresh")
    status, payload = _search_request(base, "GET", f"/{index}/_count")
    counted = json.loads(payload).get("count", 0) if status == 200 else 0
    return {"base": base, "indices": [index, notes_index], "documents": counted}
