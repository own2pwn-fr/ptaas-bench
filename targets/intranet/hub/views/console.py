"""The queue console.

Written during the night the depot handhelds stopped syncing, so that the on-call
engineer could see the job queues without waiting for a release. It has stayed where
it was put.
"""

from __future__ import annotations

from flask import Blueprint, render_template, request
from telemetry_agent import get_telemetry

from .. import db, identity

bp = Blueprint("console", __name__)

QUEUES = (
    ("payroll-export", "Payroll export", "10 past the hour"),
    ("handheld-sync", "Handheld sync", "every 5 minutes"),
    ("directory-import", "Directory import", "02:15"),
    ("claim-reminders", "Claim reminders", "07:30 weekdays"),
)


@bp.get("/admin/console/queues")
def queues():
    depth = {
        "payroll-export": db.value("SELECT COUNT(*) FROM claims WHERE stage = 'reviewed'", (), 0),
        "handheld-sync": db.value("SELECT COUNT(*) FROM assets WHERE status = 'in service'", (), 0),
        "directory-import": db.value("SELECT COUNT(*) FROM people", (), 0),
        "claim-reminders": db.value("SELECT COUNT(*) FROM claims WHERE closed = 0", (), 0),
    }
    workers = db.rows("SELECT hostname, site, status FROM assets WHERE category = 'Access point'"
                      " ORDER BY hostname LIMIT 6")
    person = identity.current()

    # Who is actually reading the console. The screens under this prefix are meant to
    # be operations only, so anyone else appearing here has walked in from outside.
    if person is None or person["role"] != "operations":
        get_telemetry().signal("intra.console.access.unscoped", {
            "viewer": person["email"] if person else "anonymous",
            "role": person["role"] if person else "-",
            "path": request.path,
            "detail": "the queue console was drawn for an account without the operations role",
        })

    return render_template("console/queues.html", queues=QUEUES, depth=depth, workers=workers)
