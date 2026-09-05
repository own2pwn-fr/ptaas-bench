"""The approvals console.

Split out of the leave module when approvals grew their own screen: one page, the
requests waiting on the signed-in approver, and a decision control per row.
"""

from __future__ import annotations

from flask import Blueprint, render_template

from .. import db, identity

bp = Blueprint("approvals", __name__)


@bp.get("/leave/approvals")
@identity.needs_person
def console():
    me = identity.current()
    waiting = db.rows(
        "SELECT r.*, p.display_name AS owner_name, p.email AS owner_email, p.team AS owner_team"
        " FROM leave_requests r JOIN people p ON p.id = r.person_id"
        " WHERE p.manager_id = ? AND r.status = 'submitted' ORDER BY r.start_date",
        (me["id"],))
    delegated = db.rows(
        "SELECT d.*, p.display_name AS from_name FROM delegations d"
        " JOIN people p ON p.id = d.person_id WHERE d.delegate_to = ?"
        " ORDER BY d.created_at DESC", (me["email"],))
    return render_template("approvals.html", waiting=waiting, delegated=delegated)
