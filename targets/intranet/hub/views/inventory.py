"""The equipment register: what we own, who holds it, and whether it answers."""

from __future__ import annotations

from flask import Blueprint, render_template, request

from .. import db, identity, markup, netprobe

bp = Blueprint("inventory", __name__)


def _asset(asset_id: int):
    return db.row(
        "SELECT a.*, p.display_name AS holder_name, p.email AS holder_email FROM assets a"
        " LEFT JOIN people p ON p.id = a.holder_id WHERE a.id = ?", (asset_id,))


@bp.get("/inventory")
@identity.needs_person
def inventory():
    categories = db.rows("SELECT category, COUNT(*) AS items FROM assets GROUP BY category"
                         " ORDER BY category")
    return render_template("inventory.html", categories=categories)


@bp.get("/parts/inventory/list")
@identity.needs_person
def asset_list():
    category = (request.args.get("category") or "").strip()
    site = (request.args.get("site") or "").strip()
    sql = ("SELECT a.*, p.display_name AS holder_name FROM assets a"
           " LEFT JOIN people p ON p.id = a.holder_id WHERE 1 = 1")
    params: list = []
    if category:
        sql += " AND a.category = ?"
        params.append(category)
    if site:
        sql += " AND a.site = ?"
        params.append(site)
    rows = db.rows(sql + " ORDER BY a.tag LIMIT 40", params)
    return render_template("parts/inventory_list.html", rows=rows, category=category, site=site)


@bp.get("/parts/inventory/categories")
@identity.needs_person
def categories():
    rows = db.rows("SELECT category, COUNT(*) AS items FROM assets GROUP BY category"
                   " ORDER BY items DESC")
    return render_template("parts/inventory_categories.html", rows=rows)


@bp.get("/parts/inventory/spares")
@identity.needs_person
def spares():
    rows = db.rows("SELECT * FROM assets WHERE status IN ('in store', 'in repair')"
                   " ORDER BY status, tag")
    return render_template("parts/inventory_spares.html", rows=rows)


@bp.get("/parts/inventory/asset/<int:asset_id>")
@identity.needs_person
def asset(asset_id: int):
    row = _asset(asset_id)
    if row is None:
        return "<p class=\"empty\">No equipment with that tag.</p>", 404
    fragment = render_template("parts/inventory_card.html", asset=row,
                               can_probe=identity.has_role("operations"))
    return markup.inspect(fragment, "asset-card",
                          signal="intra.inventory.label.attribute_escaped",
                          context={"asset": row["tag"], "route": "/parts/inventory/asset/{id}"},
                          once=f"asset-card:{asset_id}")


@bp.get("/parts/inventory/asset/<int:asset_id>/history")
@identity.needs_person
def asset_history(asset_id: int):
    row = _asset(asset_id)
    if row is None:
        return "<p class=\"empty\">No equipment with that tag.</p>", 404
    holders = db.rows(
        "SELECT display_name, team FROM people WHERE id = ? OR manager_id = ? LIMIT 5",
        (row["holder_id"] or 0, row["holder_id"] or 0))
    return render_template("parts/inventory_history.html", asset=row, holders=holders)


@bp.get("/parts/inventory/asset/<int:asset_id>/notes")
@identity.needs_person
def notes(asset_id: int):
    row = _asset(asset_id)
    if row is None:
        return "<p class=\"empty\">No equipment with that tag.</p>", 404
    items = db.rows(
        "SELECT n.*, p.display_name AS who FROM asset_notes n JOIN people p ON p.id = n.person_id"
        " WHERE n.asset_id = ? ORDER BY n.created_at", (asset_id,))
    return render_template("parts/inventory_notes.html", asset=row, items=items)


@bp.post("/parts/inventory/asset/<int:asset_id>/notes")
@identity.needs_person
def add_note(asset_id: int):
    me = identity.current()
    row = _asset(asset_id)
    if row is None:
        return "<p class=\"empty\">No equipment with that tag.</p>", 404
    if not identity.token_ok(request.form.get("ft")):
        return "<p class=\"warn\">Please reload the page and try again.</p>", 400
    body = (request.form.get("body") or "").strip()
    if not body:
        return "<p class=\"warn\">Write something first.</p>", 400
    db.write("INSERT INTO asset_notes (asset_id, person_id, body, created_at)"
             " VALUES (?, ?, ?, datetime('now'))", (asset_id, me["id"], body[:500]))
    items = db.rows(
        "SELECT n.*, p.display_name AS who FROM asset_notes n JOIN people p ON p.id = n.person_id"
        " WHERE n.asset_id = ? ORDER BY n.created_at", (asset_id,))
    return render_template("parts/inventory_notes.html", asset=row, items=items)


@bp.post("/parts/inventory/asset/<int:asset_id>/label")
@identity.needs_person
def set_label(asset_id: int):
    """Correct the label on a piece of equipment, in place from the card."""
    row = _asset(asset_id)
    if row is None:
        return "<p class=\"empty\">No equipment with that tag.</p>", 404
    label = (request.form.get("label") or "").strip()[:120]
    db.write("UPDATE assets SET label = ? WHERE id = ?", (label, asset_id))
    fresh = _asset(asset_id)
    fragment = render_template("parts/inventory_card.html", asset=fresh,
                               can_probe=identity.has_role("operations"))
    return markup.inspect(fragment, "asset-card",
                          signal="intra.inventory.label.attribute_escaped",
                          context={"asset": fresh["tag"],
                                   "route": "/parts/inventory/asset/{id}/label"},
                          once=f"asset-card:{asset_id}")


@bp.post("/parts/inventory/asset/<int:asset_id>/probe")
@identity.needs_role("operations")
def probe(asset_id: int):
    """Ask a piece of equipment whether it is on the network."""
    row = _asset(asset_id)
    if row is None:
        return "<p class=\"empty\">No equipment with that tag.</p>", 404
    host = (request.form.get("host") or row["hostname"]).strip()
    if not host or len(host) > 200:
        return "<p class=\"warn\">Give the name or address to check.</p>", 400
    result = netprobe.probe(host, row["tag"])
    return render_template("parts/inventory_probe.html", asset=row, host=host, result=result)
