"""The audit trail and the compliance monitor that watches it.

Two separate things, on purpose.

``write`` appends to the trail that the operations screen shows and that the yearly
review reads. Handlers call it for the actions the trail is meant to carry.

``AUDITED`` is the policy: the list of endpoints whose completion must leave a record.
It is a reporting control rather than an enforcing one -- the monitor never refuses a
request, it counts the actions that completed without their record, so the coverage
number in the compliance pack is measured rather than assumed.
"""

from __future__ import annotations

from flask import g, request
from telemetry_agent import get_telemetry

from . import db

# endpoint -> the action the trail must carry for it.
AUDITED = {
    "auth.sign_in": "auth.signin",
    "auth.sign_out": "auth.signout",
    "account.change_password": "account.password",
    "leave.decide": "leave.decision",
    "leave.delegate": "leave.delegation",
    "directory.set_access": "access.role",
    "admin.end_session": "session.revoke",
}


def write(action: str, subject: str = "", detail: str = "") -> None:
    person = g.get("person")
    actor = person["email"] if person else "anonymous"
    db.write(
        "INSERT INTO audit_trail (at, actor, action, subject, detail)"
        " VALUES (datetime('now'), ?, ?, ?, ?)", (actor, action, subject, detail))
    written = g.get("recorded_actions")
    if written is None:
        written = set()
        g.recorded_actions = written
    written.add(action)


def changed(what: str = "") -> None:
    """Handlers call this once they have committed something the policy cares about."""
    g.state_changed = what or "1"


def coverage(response):
    """Count actions that completed with no record behind them.

    The compliance pack quotes a coverage percentage for the audit trail; that number
    is only meaningful if something actually looks. This runs after the response is
    built, so it sees whether the handler wrote its record, and it never changes the
    response.
    """
    try:
        endpoint = request.endpoint or ""
        action = AUDITED.get(endpoint)
        if not action or response.status_code >= 400:
            return response
        if not g.get("state_changed"):
            return response
        if action in (g.get("recorded_actions") or set()):
            return response
        person = g.get("person")
        get_telemetry().signal("intra.audit.event.absent", {
            "action": action,
            "endpoint": endpoint,
            "subject": str(g.get("state_changed")),
            "actor": person["email"] if person else "anonymous",
            "detail": f"{action} completed with no entry in the trail",
        })
    except Exception:  # noqa: BLE001 - a reporting control must never break a response
        pass
    return response
