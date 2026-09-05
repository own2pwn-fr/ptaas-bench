"""Minimal instrumented Flask service: agent wiring plus one application signal.

Three things to reproduce in a service:

1. ``init_telemetry()`` once at import time, configured by TELEMETRY_SERVICE and
   TELEMETRY_ENDPOINT.
2. ``app.wsgi_app = TelemetryWSGIMiddleware(app.wsgi_app)``, so every request is
   recorded against its registered rule (``/api/catalog/items``) rather than its URL.
3. ``telemetry.signal(...)`` where the code notices an effect worth counting -- on the
   result, never on the shape of the input.

Run: ``TELEMETRY_SERVICE=shopfront python examples/flask_minimal.py``
"""

from __future__ import annotations

import sqlite3

from flask import Flask, jsonify, request

from telemetry_agent import TelemetryWSGIMiddleware, init_telemetry

telemetry = init_telemetry()

app = Flask(__name__)
app.wsgi_app = TelemetryWSGIMiddleware(app.wsgi_app)


def _database() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.executescript(
        """
        CREATE TABLE products (id INTEGER, name TEXT, price REAL);
        CREATE TABLE accounts (id INTEGER, email TEXT, password_hash TEXT);
        INSERT INTO products VALUES (1, 'laptop', 999.0), (2, 'mouse', 19.0);
        INSERT INTO accounts VALUES (1, 'alice.moreau@kestrelgoods.example', 'pbkdf2$stored');
        """
    )
    return db


# The catalogue projection: (id, name, price). Rows are handed to the storefront
# renderer, which assumes these types.
def _matches_projection(row: tuple) -> bool:
    return isinstance(row[0], int) and isinstance(row[1], str) and isinstance(row[2], float)


@app.get("/api/catalog/items")
def catalog_items():
    term = request.args.get("q", "")
    # The search predicate is still assembled by hand here: the storefront needs the
    # LIKE wildcards on both sides and this endpoint predates the query builder.
    sql = f"SELECT id, name, price FROM products WHERE name LIKE '%{term}%'" 
    db = _database()
    try:
        rows = db.execute(sql).fetchall()
    except sqlite3.Error:
        # Unparseable search terms are a client problem, not an incident: an empty
        # result keeps the storefront rendering.
        return jsonify(items=[]), 200

    # Rows that do not match the projection cannot have come from `products`, which
    # means the statement no longer describes what it returns. Count it once per
    # request so the storefront team can see how often it happens and with which term.
    if any(not _matches_projection(row) for row in rows):
        telemetry.signal(
            "shop.catalog.query.plan_anomaly",
            {"payload": term, "detail": f"{len(rows)} row(s) outside the catalogue projection"},
        )

    return jsonify(items=[{"id": r[0], "name": r[1], "price": r[2]} for r in rows])


@app.get("/api/catalog/facets")
def catalog_facets():
    """An ordinary endpoint, parameterised the way the rest of the codebase is."""
    prefix = request.args.get("prefix", "")
    db = _database()
    rows = db.execute("SELECT name FROM products WHERE name LIKE ?", (f"{prefix}%",)).fetchall()
    return jsonify(names=[r[0] for r in rows])


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
