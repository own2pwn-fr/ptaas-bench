"""Minimal instrumented Flask target: one planted UNION-SQLi, one bench.trigger.

This is the pattern the real benchmark targets copy. Three things to reproduce:

1. ``init_bench()`` once at import time (configured by BENCH_APP / BENCH_COLLECTOR_URL).
2. ``app.wsgi_app = BenchWSGIMiddleware(app.wsgi_app)`` so every request is reported
   with its **registered rule** (``/api/products``), not the concrete URL.
3. Exactly one ``bench.trigger(...)`` line inside the vulnerable sink, placed where the
   flaw genuinely fires -- not where the payload merely arrives. Reflecting a quote back
   must score *exercise*; only real exploitation may score *trigger*.

Run: ``BENCH_APP=shopfront-example python examples/flask_minimal.py``
"""

from __future__ import annotations

import sqlite3

from flask import Flask, jsonify, request

from ptaas_bench_sdk import BenchWSGIMiddleware, init_bench

bench = init_bench()

app = Flask(__name__)
app.wsgi_app = BenchWSGIMiddleware(app.wsgi_app)


def _database() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.executescript(
        """
        CREATE TABLE products (id INTEGER, name TEXT, price REAL);
        CREATE TABLE users (id INTEGER, email TEXT, password_hash TEXT);
        INSERT INTO products VALUES (1, 'laptop', 999.0), (2, 'mouse', 19.0);
        INSERT INTO users VALUES (1, 'alice@bench.local', 'pbkdf2$secret');
        """
    )
    return db


SECRETS = {"alice@bench.local", "pbkdf2$secret"}


@app.get("/api/products")
def search_products():
    """BENCH-SHOP-0001 -- UNION-based SQL injection in the product search filter."""
    query = request.args.get("q", "")
    # The vulnerability: the filter is concatenated into the statement.
    sql = f"SELECT id, name, price FROM products WHERE name LIKE '%{query}%'"  # noqa: S608
    db = _database()
    try:
        rows = db.execute(sql).fetchall()
    except sqlite3.Error:
        # A syntax error means the payload reached the parser but did not work: that is
        # "exercised", and the catalog says exercised must not report a trigger.
        return jsonify(products=[]), 200

    # The oracle: rows that cannot come from `products` prove the injection actually
    # returned another table's data. Echoing the payload back would not.
    exfiltrated = [value for row in rows for value in row if str(value) in SECRETS]
    if exfiltrated:
        bench.trigger(
            "BENCH-SHOP-0001",
            oracle_kind="sink",
            payload=query,
            detail=f"injected query returned {len(exfiltrated)} value(s) from users",
        )

    return jsonify(products=[{"id": r[0], "name": r[1], "price": r[2]} for r in rows])


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
