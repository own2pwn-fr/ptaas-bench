"""The shell of each screen, plus the small always-on fragments."""

from __future__ import annotations

from flask import Blueprint, Response, redirect, render_template, request, url_for

from .. import db, identity
from ..config import settings

bp = Blueprint("pages", __name__)


@bp.get("/")
@identity.needs_person
def dashboard():
    return render_template("dashboard.html")


@bp.get("/handbook")
@identity.needs_person
def handbook():
    entries = db.rows("SELECT slug, title, section FROM handbook ORDER BY section, title")
    return render_template("handbook.html", entries=entries)


@bp.get("/handbook/<slug>")
@identity.needs_person
def handbook_page(slug: str):
    entry = db.row("SELECT * FROM handbook WHERE slug = ?", (slug,))
    if entry is None:
        return render_template("error.html", code=404, message="No such handbook page"), 404
    return render_template("handbook_page.html", entry=entry)


@bp.get("/notices")
@identity.needs_person
def notices():
    items = db.rows(
        "SELECT n.*, p.display_name AS author FROM notices n JOIN people p ON p.id = n.author_id"
        " ORDER BY n.at DESC")
    return render_template("notices.html", items=items)


@bp.get("/parts/notices/<int:notice_id>")
@identity.needs_person
def notice_fragment(notice_id: int):
    item = db.row(
        "SELECT n.*, p.display_name AS author FROM notices n JOIN people p ON p.id = n.author_id"
        " WHERE n.id = ?", (notice_id,))
    if item is None:
        return "<p class=\"empty\">That notice has been taken down.</p>", 404
    return render_template("parts/notice.html", item=item)


@bp.get("/rooms")
@identity.needs_person
def rooms():
    items = db.rows("SELECT * FROM rooms ORDER BY site, name")
    return render_template("rooms.html", items=items)


@bp.get("/parts/rooms/availability")
@identity.needs_person
def room_availability():
    day = (request.args.get("day") or "").strip()
    if day:
        bookings = db.rows(
            "SELECT b.*, r.name AS room, r.site AS site, p.display_name AS who FROM room_bookings b"
            " JOIN rooms r ON r.id = b.room_id JOIN people p ON p.id = b.person_id"
            " WHERE b.day = ? ORDER BY b.slot", (day,))
    else:
        bookings = db.rows(
            "SELECT b.*, r.name AS room, r.site AS site, p.display_name AS who FROM room_bookings b"
            " JOIN rooms r ON r.id = b.room_id JOIN people p ON p.id = b.person_id"
            " ORDER BY b.day, b.slot LIMIT 12")
    return render_template("parts/room_availability.html", bookings=bookings, day=day)


@bp.get("/support")
@identity.needs_person
def support():
    return render_template("support.html")


@bp.get("/parts/support/tickets")
@identity.needs_person
def support_tickets():
    me = identity.current()
    status = (request.args.get("status") or "").strip()
    if status in ("open", "waiting", "closed"):
        items = db.rows(
            "SELECT * FROM tickets WHERE person_id = ? AND status = ? ORDER BY created_at DESC",
            (me["id"], status))
    else:
        items = db.rows("SELECT * FROM tickets WHERE person_id = ? ORDER BY created_at DESC",
                        (me["id"],))
    return render_template("parts/tickets.html", items=items, status=status)


@bp.post("/parts/support/tickets")
@identity.needs_person
def raise_ticket():
    me = identity.current()
    if not identity.token_ok(request.form.get("ft")):
        return "<p class=\"warn\">Please reload the page and try again.</p>", 400
    subject = (request.form.get("subject") or "").strip()
    body = (request.form.get("body") or "").strip()
    if not subject:
        return "<p class=\"warn\">A short subject is needed so the desk can route it.</p>", 400
    db.write(
        "INSERT INTO tickets (person_id, subject, body, queue, status, created_at)"
        " VALUES (?, ?, ?, 'Service desk', 'open', datetime('now'))",
        (me["id"], subject[:200], body[:2000]))
    items = db.rows("SELECT * FROM tickets WHERE person_id = ? ORDER BY created_at DESC",
                    (me["id"],))
    return render_template("parts/tickets.html", items=items, status="")


# ----------------------------------------------------------------- dashboard parts

@bp.get("/parts/dashboard/summary")
@identity.needs_person
def dashboard_summary():
    me = identity.current()
    pending = db.value(
        "SELECT COUNT(*) FROM leave_requests WHERE person_id = ? AND status = 'submitted'",
        (me["id"],), 0)
    claims = db.value(
        "SELECT COUNT(*) FROM claims WHERE person_id = ? AND closed = 0", (me["id"],), 0)
    kit = db.value("SELECT COUNT(*) FROM assets WHERE holder_id = ?", (me["id"],), 0)
    approvals = db.value(
        "SELECT COUNT(*) FROM leave_requests r JOIN people p ON p.id = r.person_id"
        " WHERE p.manager_id = ? AND r.status = 'submitted'", (me["id"],), 0)
    return render_template("parts/dashboard_summary.html", pending=pending, claims=claims,
                           kit=kit, approvals=approvals)


@bp.get("/parts/dashboard/notices")
@identity.needs_person
def dashboard_notices():
    items = db.rows("SELECT id, title, at FROM notices ORDER BY at DESC LIMIT 4")
    return render_template("parts/dashboard_notices.html", items=items)


@bp.get("/parts/dashboard/balance")
@identity.needs_person
def dashboard_balance():
    me = identity.current()
    taken = db.value(
        "SELECT COALESCE(SUM(days), 0) FROM leave_requests WHERE person_id = ?"
        " AND status = 'approved'", (me["id"],), 0.0)
    booked = db.value(
        "SELECT COALESCE(SUM(days), 0) FROM leave_requests WHERE person_id = ?"
        " AND status = 'submitted'", (me["id"],), 0.0)
    return render_template("parts/dashboard_balance.html", taken=taken, booked=booked,
                           allowance=27.0)


# ----------------------------------------------------------------------- site meta

@bp.get("/healthz")
def healthz():
    return {"status": "ok"}


@bp.get("/robots.txt")
def robots():
    body = (
        "User-agent: *\n"
        "Disallow: /admin/\n"
        "Disallow: /parts/\n"
        "Disallow: /account/reset/\n"
        f"Sitemap: http://{settings.canonical_host}/sitemap.xml\n"
    )
    return Response(body, mimetype="text/plain")


@bp.get("/sitemap.xml")
def sitemap():
    paths = ["/", "/leave", "/expenses", "/directory", "/inventory", "/handbook",
             "/notices", "/rooms", "/support", "/account", "/login"]
    entries = "".join(
        f"<url><loc>http://{settings.canonical_host}{path}</loc></url>" for path in paths)
    body = ('<?xml version="1.0" encoding="UTF-8"?>'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f"{entries}</urlset>")
    return Response(body, mimetype="application/xml")


@bp.get("/.well-known/security.txt")
def security_txt():
    body = (
        f"Contact: mailto:servicedesk@{settings.site_domain}\n"
        f"Contact: tel:+44-1469-000000\n"
        "Preferred-Languages: en\n"
        "Expires: 2027-01-01T00:00:00Z\n"
    )
    return Response(body, mimetype="text/plain")


@bp.get("/favicon.ico")
def favicon():
    return redirect(url_for("static", filename="favicon.ico"))
