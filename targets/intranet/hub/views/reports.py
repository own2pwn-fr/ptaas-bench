"""Scheduled exports.

The payroll provider collects the headcount file over the private link every night;
the route exists so that collection has somewhere to point.
"""

from __future__ import annotations

import csv
import io

from flask import Blueprint, Response, render_template, request
from telemetry_agent import get_telemetry

from .. import db, identity

bp = Blueprint("reports", __name__, url_prefix="/admin/reports")


@bp.get("/")
def index():
    return render_template("admin/reports.html")


@bp.get("/headcount.csv")
def headcount():
    rows = db.rows(
        "SELECT p.id, p.display_name, p.email, p.team, p.job_title, p.site, p.salary_band,"
        " p.postcode, m.email AS manager FROM people p LEFT JOIN people m ON m.id = p.manager_id"
        " ORDER BY p.id")
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["staff_number", "name", "address", "team", "role", "site", "band",
                     "postcode", "manager"])
    for row in rows:
        writer.writerow([row["id"], row["display_name"], row["email"], row["team"],
                         row["job_title"], row["site"], row["salary_band"], row["postcode"],
                         row["manager"] or ""])

    person = identity.current()
    if person is None or person["role"] != "operations":
        get_telemetry().signal("intra.reports.export.unscoped", {
            "viewer": person["email"] if person else "anonymous",
            "rows": len(rows),
            "path": request.path,
            "detail": f"the headcount file ({len(rows)} rows) was collected without the "
                      "operations role",
        })

    return Response(buffer.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=headcount.csv"})
