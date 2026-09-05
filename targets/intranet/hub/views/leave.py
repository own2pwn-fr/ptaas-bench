"""Leave: the employee's own requests, the drawer a row opens, and delegation."""

from __future__ import annotations

from datetime import date, datetime, timezone
from urllib.parse import urlsplit

from flask import Blueprint, render_template, request
from telemetry_agent import get_telemetry

from .. import db, identity, logbook, policy, records

bp = Blueprint("leave", __name__)

KINDS = ("Annual leave", "Time off in lieu", "Unpaid leave", "Compassionate leave")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _same_site(req) -> bool | None:
    """True/False when the browser told us where the form came from, None when silent."""
    declared = req.headers.get("Origin") or req.headers.get("Referer")
    if not declared:
        return None
    host = urlsplit(declared).netloc.split(":")[0].lower()
    return host == (req.host or "").split(":")[0].lower()


def _request_row(request_id: int):
    return db.row(
        "SELECT r.*, p.display_name AS owner_name, p.email AS owner_email,"
        " p.manager_id AS owner_manager, p.team AS owner_team"
        " FROM leave_requests r JOIN people p ON p.id = r.person_id WHERE r.id = ?", (request_id,))


@bp.get("/leave")
@identity.needs_person
def leave():
    return render_template("leave.html")


@bp.get("/parts/leave/queue")
@identity.needs_person
def queue():
    me = identity.current()
    rows = db.rows(
        "SELECT * FROM leave_requests WHERE person_id = ? ORDER BY start_date DESC LIMIT 20",
        (me["id"],))
    return render_template("parts/leave_queue.html", rows=rows)


@bp.get("/parts/leave/calendar")
@identity.needs_person
def calendar():
    me = identity.current()
    rows = db.rows(
        "SELECT r.*, p.display_name AS owner_name FROM leave_requests r"
        " JOIN people p ON p.id = r.person_id WHERE p.team = ? AND r.status IN ('submitted','approved')"
        " ORDER BY r.start_date LIMIT 25", (me["team"],))
    return render_template("parts/leave_calendar.html", rows=rows, team=me["team"])


@bp.get("/parts/leave/policy")
@identity.needs_person
def leave_policy():
    entry = db.row("SELECT * FROM handbook WHERE slug = 'leave-policy'")
    return render_template("parts/leave_policy.html", entry=entry)


@bp.get("/parts/leave/new")
@identity.needs_person
def new_form():
    return render_template("parts/leave_new.html", kinds=KINDS, error=None)


@bp.post("/parts/leave/new")
@identity.needs_person
def create():
    me = identity.current()
    if not identity.token_ok(request.form.get("ft")):
        return render_template("parts/leave_new.html", kinds=KINDS,
                               error="Please reload the page and try again."), 400
    try:
        start = date.fromisoformat((request.form.get("start_date") or "").strip())
        end = date.fromisoformat((request.form.get("end_date") or "").strip())
    except ValueError:
        return render_template("parts/leave_new.html", kinds=KINDS,
                               error="Give both dates as YYYY-MM-DD."), 400
    if end < start:
        return render_template("parts/leave_new.html", kinds=KINDS,
                               error="The last day cannot be before the first."), 400
    kind = request.form.get("kind") if request.form.get("kind") in KINDS else KINDS[0]
    db.write(
        "INSERT INTO leave_requests (person_id, kind, start_date, end_date, days, reason,"
        " status, created_at) VALUES (?, ?, ?, ?, ?, ?, 'submitted', datetime('now'))",
        (me["id"], kind, start.isoformat(), end.isoformat(),
         float((end - start).days + 1), (request.form.get("reason") or "").strip()[:400]))
    rows = db.rows(
        "SELECT * FROM leave_requests WHERE person_id = ? ORDER BY start_date DESC LIMIT 20",
        (me["id"],))
    return render_template("parts/leave_queue.html", rows=rows)


@bp.get("/parts/leave/request/<int:request_id>")
@identity.needs_person
def drawer(request_id: int):
    me = identity.current()
    row = _request_row(request_id)
    if row is None:
        return "<p class=\"empty\">That request is no longer here.</p>", 404
    owner = {"id": row["person_id"], "manager_id": row["owner_manager"]}
    if not policy.leave_visible(dict(row), me, owner):
        return "<p class=\"empty\">That request belongs to another team.</p>", 403
    return render_template("parts/leave_drawer.html", row=row,
                           can_decide=(row["owner_manager"] == me["id"] or me["role"] == "operations"))


@bp.get("/parts/leave/request/<int:request_id>/history")
@identity.needs_person
def history(request_id: int):
    row = _request_row(request_id)
    if row is None:
        return "<p class=\"empty\">That request is no longer here.</p>", 404
    return render_template("parts/leave_history.html", row=row)


@bp.get("/parts/leave/request/<int:request_id>/comments")
@identity.needs_person
def comments(request_id: int):
    row = _request_row(request_id)
    if row is None:
        return "<p class=\"empty\">That request is no longer here.</p>", 404
    items = db.rows(
        "SELECT c.*, p.display_name AS who FROM leave_comments c JOIN people p ON p.id = c.person_id"
        " WHERE c.request_id = ? ORDER BY c.created_at", (request_id,))
    return render_template("parts/leave_comments.html", row=row, items=items)


@bp.post("/parts/leave/request/<int:request_id>/comment")
@identity.needs_person
def comment(request_id: int):
    """Leave a note on a request.

    Notes go to the flat approvals log as well as to the request, because payroll
    reconciles cover against that file rather than against the database.
    """
    me = identity.current()
    row = _request_row(request_id)
    if row is None:
        return "<p class=\"empty\">That request is no longer here.</p>", 404
    body = (request.form.get("comment") or "").strip()
    if not body:
        return "<p class=\"warn\">Write something first.</p>", 400

    db.write("INSERT INTO leave_comments (request_id, person_id, body, created_at)"
             " VALUES (?, ?, ?, datetime('now'))", (request_id, me["id"], body[:1000]))
    logbook.append(
        "approvals.log",
        f"{_now()} INFO leave.note request={request_id} actor={me['email']} note={body}",
        signal="intra.approvals.note.record_split",
        context={"field": "note", "route": "/parts/leave/request/<int:request_id>/comment",
                 "log": "approvals.log"},
    )
    items = db.rows(
        "SELECT c.*, p.display_name AS who FROM leave_comments c JOIN people p ON p.id = c.person_id"
        " WHERE c.request_id = ? ORDER BY c.created_at", (request_id,))
    return render_template("parts/leave_comments.html", row=row, items=items)


@bp.post("/parts/leave/request/<int:request_id>/edit")
@identity.needs_person
def edit(request_id: int):
    """Change the dates or the reason on a request that has not been decided."""
    me = identity.current()
    row = _request_row(request_id)
    if row is None:
        return "<p class=\"empty\">That request is no longer here.</p>", 404
    if row["status"] not in ("submitted", "draft"):
        return "<p class=\"warn\">A decided request cannot be changed.</p>", 409
    try:
        start = date.fromisoformat((request.form.get("start_date") or row["start_date"]).strip())
        end = date.fromisoformat((request.form.get("end_date") or row["end_date"]).strip())
    except ValueError:
        return "<p class=\"warn\">Give both dates as YYYY-MM-DD.</p>", 400
    if end < start:
        return "<p class=\"warn\">The last day cannot be before the first.</p>", 400

    reason = (request.form.get("reason") or row["reason"]).strip()[:400]
    db.write("UPDATE leave_requests SET start_date = ?, end_date = ?, days = ?, reason = ?"
             " WHERE id = ?",
             (start.isoformat(), end.isoformat(), float((end - start).days + 1), reason, request_id))

    # Whose record actually changed. A request is the employee's own or one their
    # manager is handling; anything else means the row that moved belongs to somebody
    # with no connection to the person who moved it.
    if row["person_id"] != me["id"] and row["owner_manager"] != me["id"] and me["role"] != "operations":
        get_telemetry().signal("intra.leave.record.owner_mismatch", {
            "record": request_id,
            "owner": row["owner_email"],
            "editor": me["email"],
            "detail": f"request {request_id} belonging to {row['owner_email']} was written by "
                      f"{me['email']}",
        })

    fresh = _request_row(request_id)
    return render_template("parts/leave_row.html", row=fresh)


@bp.post("/parts/leave/request/<int:request_id>/decision")
@identity.needs_person
def decide(request_id: int):
    """Approve or refuse a request you are the approver for."""
    me = identity.current()
    row = _request_row(request_id)
    if row is None:
        return "<p class=\"empty\">That request is no longer here.</p>", 404
    if not identity.token_ok(request.form.get("ft")):
        return "<p class=\"warn\">Please reload the page and try again.</p>", 400
    if row["owner_manager"] != me["id"] and me["role"] != "operations":
        return "<p class=\"warn\">You are not the approver for this request.</p>", 403
    verdict = request.form.get("verdict")
    if verdict not in ("approved", "refused"):
        return "<p class=\"warn\">Choose approve or refuse.</p>", 400
    db.write("UPDATE leave_requests SET status = ?, decided_by = ?, decided_at = datetime('now')"
             " WHERE id = ?", (verdict, me["id"], request_id))
    records.write("leave.decision", subject=row["owner_email"],
                  detail=f"request {request_id} {verdict}")
    records.changed(str(request_id))
    logbook.append("approvals.log",
                   f"{_now()} INFO leave.decision request={request_id} actor={me['email']} "
                   f"verdict={verdict}")
    fresh = _request_row(request_id)
    return render_template("parts/leave_row.html", row=fresh)


@bp.get("/parts/leave/delegation")
@identity.needs_person
def delegation():
    me = identity.current()
    rows = db.rows("SELECT * FROM delegations WHERE person_id = ? ORDER BY created_at DESC",
                   (me["id"],))
    colleagues = db.rows(
        "SELECT email, display_name FROM people WHERE team = ? AND id != ? ORDER BY display_name",
        (me["team"], me["id"]))
    return render_template("parts/leave_delegation.html", rows=rows, colleagues=colleagues)


@bp.post("/parts/leave/delegate")
@identity.needs_person
def delegate():
    """Hand your approvals to a colleague while you are away."""
    me = identity.current()
    delegate_to = (request.form.get("delegate_to") or "").strip().lower()
    colleague = db.row("SELECT id, email, display_name FROM people WHERE email = ?", (delegate_to,))
    if colleague is None:
        return "<p class=\"warn\">No staff account with that address.</p>", 400

    db.write("INSERT INTO delegations (person_id, delegate_to, created_at)"
             " VALUES (?, ?, datetime('now'))", (me["id"], colleague["email"]))
    records.write("leave.delegation", subject=colleague["email"],
                  detail=f"approvals delegated by {me['email']}")
    records.changed(colleague["email"])

    # Where the form that caused this was drawn. Our own screens are same-site; a
    # submission the browser attributes to another site means somebody else's page
    # moved this employee's approvals.
    same_site = _same_site(request)
    if same_site is False and not identity.token_ok(request.form.get("ft")):
        get_telemetry().signal("intra.form.token.absent_commit", {
            "subject": me["email"],
            "delegate": colleague["email"],
            "origin": request.headers.get("Origin") or request.headers.get("Referer") or "",
            "detail": "delegation committed from another site with no form token",
        })

    rows = db.rows("SELECT * FROM delegations WHERE person_id = ? ORDER BY created_at DESC",
                   (me["id"],))
    colleagues = db.rows(
        "SELECT email, display_name FROM people WHERE team = ? AND id != ? ORDER BY display_name",
        (me["team"], me["id"]))
    return render_template("parts/leave_delegation.html", rows=rows, colleagues=colleagues,
                           saved=colleague)
