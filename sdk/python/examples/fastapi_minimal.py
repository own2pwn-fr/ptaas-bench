"""Minimal instrumented FastAPI service: agent wiring, one signal, one dependency link.

Same wiring as the Flask example with the ASGI middleware, plus the two calls that make
per-subject and per-dependency behaviour explicable after the fact:

* ``telemetry.set_auth_subject(...)`` so the request record says who was served;
* ``telemetry.outbound(url, ...)`` immediately before an outbound fetch whose
  destination came from the request, so the egress the network sees can be tied back.

Run: ``TELEMETRY_SERVICE=shopfront uvicorn examples.fastapi_minimal:app``
"""

from __future__ import annotations

import urllib.request

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from telemetry_agent import TelemetryASGIMiddleware, init_telemetry

telemetry = init_telemetry()

app = FastAPI()
app.add_middleware(TelemetryASGIMiddleware, framework_app=app)

SESSIONS = {"alice": 1, "raphael": 2}
ORDERS = {
    1001: {"id": 1001, "customer_id": 1, "total": 999.0},
    1002: {"id": 1002, "customer_id": 2, "total": 19.0},
}


def _subject(cookie: str | None) -> int:
    customer_id = SESSIONS.get((cookie or "").replace("sid=", "").strip())
    if customer_id is None:
        raise HTTPException(status_code=401, detail="unauthenticated")
    telemetry.set_auth_subject(f"customer:{customer_id}")
    return customer_id


@app.get("/api/orders/{id}")
def get_order(id: int, cookie: str | None = Header(default=None)):  # noqa: A002
    customer_id = _subject(cookie)
    # Orders are read by primary key: the id comes from the account's own order list.
    order = ORDERS.get(id)
    if order is None:
        raise HTTPException(status_code=404, detail="not found")

    # Serving a row whose customer is not the session's means the order list and the
    # session disagree. Counted on the row about to be returned, so the counter moves
    # on what was actually served rather than on what was asked for.
    if order["customer_id"] != customer_id:
        telemetry.signal(
            "shop.orders.subject.mismatch",
            {
                "payload": str(id),
                "detail": f"customer:{customer_id} served an order of customer:{order['customer_id']}",
            },
        )
    return order


class ImportRequest(BaseModel):
    source_url: str


@app.post("/api/admin/imports")
def start_import(body: ImportRequest, cookie: str | None = Header(default=None)):
    """Pull a supplier catalogue feed. Suppliers host their own feed URLs."""
    _subject(cookie)
    # The destination comes from the request, so the pairing is registered before the
    # fetch: the connection itself is only visible in the network's logs, where nothing
    # says which request opened it.
    telemetry.outbound(body.source_url, signal="shop.imports.fetch.external", param="source_url")
    _fetch(body.source_url)
    return {"status": "accepted"}


def _fetch(url: str) -> None:
    """Fetch the feed and discard the body; the importer only needs the freshness."""
    try:
        with urllib.request.urlopen(url, timeout=5):
            pass
    except Exception:  # noqa: BLE001 - a supplier being down is not our failure
        pass
