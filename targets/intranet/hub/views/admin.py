"""Operations screens: people, the audit trail and the overnight jobs."""

from __future__ import annotations

from flask import Blueprint, redirect, render_template, request, url_for

from .. import db, identity, logbook, records

bp = Blueprint("admin", __name__, url_prefix="/admin")


@bp.before_request
def _operations_only():
    if identity.current() is None:
        return redirect(url_for("auth.login", next=request.path))
    if not identity.has_role("operations"):
        return render_template("error.html", code=403,
                               message="Your account does not cover the operations screens"), 403
    return None


@bp.get("/")
def home():
    counts = {
        "people": db.value("SELECT COUNT(*) FROM people", (), 0),
        "assets": db.value("SELECT COUNT(*) FROM assets", (), 0),
        "open_claims": db.value("SELECT COUNT(*) FROM claims WHERE closed = 0", (), 0),
        "waiting": db.value("SELECT COUNT(*) FROM leave_requests WHERE status = 'submitted'", (), 0),
    }
    return render_template("admin/home.html", counts=counts)


@bp.get("/people")
def people():
    rows = db.rows("SELECT id, display_name, email, team, role, site FROM people"
                   " ORDER BY display_name")
    return render_template("admin/people.html", rows=rows)


@bp.get("/audit")
def audit():
    rows = db.rows("SELECT * FROM audit_trail ORDER BY id DESC LIMIT 50")
    return render_template("admin/audit.html", rows=rows)


@bp.get("/parts/audit")
def audit_fragment():
    action = (request.args.get("action") or "").strip()
    if action:
        rows = db.rows("SELECT * FROM audit_trail WHERE action = ? ORDER BY id DESC LIMIT 50",
                       (action,))
    else:
        rows = db.rows("SELECT * FROM audit_trail ORDER BY id DESC LIMIT 50")
    return render_template("parts/audit_rows.html", rows=rows, action=action)


@bp.get("/jobs")
def jobs():
    return render_template("admin/jobs.html", recent=logbook.tail("approvals.log", 12))


@bp.post("/sessions/<session_id>/end")
def end_session(session_id: str):
    row = db.row("SELECT s.id, p.email AS email FROM sessions s JOIN people p ON p.id = s.person_id"
                 " WHERE s.id = ?", (session_id,))
    if row is None:
        return "<p class=\"empty\">No such session.</p>", 404
    identity.end(session_id)
    records.write("session.revoke", subject=row["email"], detail="ended from the operations screen")
    records.changed(row["email"])
    return "<p class=\"ok\">That session has been ended.</p>"
