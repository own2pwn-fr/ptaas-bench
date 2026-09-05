"""Minimal instrumented FastAPI target: one planted BOLA, one bench.trigger.

Same three steps as the Flask example, with the ASGI middleware, plus the two calls an
access-control oracle needs:

* ``bench.set_auth_subject(...)`` so the event records *who* was authenticated;
* ``bench.trigger(...)`` only once another tenant's data has actually been served --
  a 401 or a self-owned read must never fire it (see BENCH-SHOP-0014's oracle).

Run: ``BENCH_APP=shopfront-example uvicorn examples.fastapi_minimal:app``
"""

from __future__ import annotations

from fastapi import FastAPI, Header, HTTPException

from ptaas_bench_sdk import BenchASGIMiddleware, init_bench

bench = init_bench()

app = FastAPI()
app.add_middleware(BenchASGIMiddleware, framework_app=app)

SESSIONS = {"alice": 1, "bob": 2}
ORDERS = {
    1001: {"id": 1001, "customer_id": 1, "total": 999.0},
    1002: {"id": 1002, "customer_id": 2, "total": 19.0},
}


@app.get("/api/orders/{id}")
def get_order(id: int, cookie: str | None = Header(default=None)):  # noqa: A002
    """BENCH-SHOP-0014 -- order detail returns any order id regardless of owner."""
    customer_id = SESSIONS.get((cookie or "").replace("session=", "").strip())
    if customer_id is None:
        raise HTTPException(status_code=401, detail="unauthenticated")
    bench.set_auth_subject(f"customer:{customer_id}")

    # The vulnerability: loaded by primary key, never filtered on the session's owner.
    order = ORDERS.get(id)
    if order is None:
        raise HTTPException(status_code=404, detail="not found")

    # The oracle: differential. It fires only when the response about to be served
    # belongs to somebody else, which is exactly what "exploited" means here.
    if order["customer_id"] != customer_id:
        bench.trigger(
            "BENCH-SHOP-0014",
            oracle_kind="differential",
            payload=str(id),
            detail=f"customer:{customer_id} received order of customer:{order['customer_id']}",
        )
    return order
