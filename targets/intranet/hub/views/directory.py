"""The people directory: who is who, who reports to whom, and what they may reach."""

from __future__ import annotations

from flask import Blueprint, render_template, request

from .. import db, identity, markup, records

bp = Blueprint("directory", __name__)

ROLES = ("staff", "approver", "operations")


@bp.get("/directory")
@identity.needs_person
def directory():
    teams = db.rows("SELECT team, COUNT(*) AS people FROM people GROUP BY team ORDER BY team")
    return render_template("directory.html", teams=teams)


ORDERS = {"az": "display_name", "za": "display_name DESC", "team": "team, display_name",
          "site": "site, display_name"}


@bp.get("/parts/directory/list")
@identity.needs_person
def people_list():
    team = (request.args.get("team") or "").strip()
    order = ORDERS.get((request.args.get("sort") or "az").strip(), ORDERS["az"])
    if team:
        rows = db.rows(
            "SELECT id, display_name, job_title, team, site, extension FROM people"
            f" WHERE team = ? ORDER BY {order}", (team,))
    else:
        rows = db.rows(
            "SELECT id, display_name, job_title, team, site, extension FROM people"
            f" ORDER BY {order} LIMIT 30")
    return render_template("parts/directory_list.html", rows=rows, team=team)


@bp.get("/parts/directory/search")
@identity.needs_person
def search():
    term = (request.args.get("q") or "").strip()
    rows = db.rows(
        "SELECT id, display_name, job_title, team, site, extension FROM people"
        " WHERE display_name LIKE ? OR job_title LIKE ? OR team LIKE ?"
        " ORDER BY display_name LIMIT 20",
        (f"%{term}%", f"%{term}%", f"%{term}%")) if term else []
    return render_template("parts/directory_search.html", rows=rows, term=term)


@bp.get("/parts/directory/teams")
@identity.needs_person
def teams():
    rows = db.rows(
        "SELECT team, COUNT(*) AS people, SUM(role != 'staff') AS approvers FROM people"
        " GROUP BY team ORDER BY team")
    return render_template("parts/directory_teams.html", rows=rows)


@bp.get("/parts/directory/filter")
@identity.needs_person
def team_filter():
    """The chip that shows which team the list is narrowed to.

    Hand-written during a hotfix when the design system's chip had no dismiss button.
    """
    team = (request.args.get("team") or "").strip()
    sort = (request.args.get("sort") or "az").strip()
    people = db.value("SELECT COUNT(*) FROM people WHERE team = ?", (team,), 0)
    fragment = render_template("parts/directory_chip.html", team=team, sort=sort, people=people)
    return markup.inspect(fragment, "directory-chip",
                          signal="intra.render.attribute.escaped",
                          context={"route": "/parts/directory/filter", "value_len": len(sort)})


@bp.get("/parts/directory/person/<int:person_id>")
@identity.needs_person
def person(person_id: int):
    row = db.row(
        "SELECT p.*, m.display_name AS manager_name FROM people p"
        " LEFT JOIN people m ON m.id = p.manager_id WHERE p.id = ?", (person_id,))
    if row is None:
        return "<p class=\"empty\">Nobody with that staff number.</p>", 404
    kit = db.rows("SELECT id, tag, model, category FROM assets WHERE holder_id = ?", (person_id,))
    return render_template("parts/directory_person.html", person=row, kit=kit,
                           can_set_access=identity.has_role("operations"), roles=ROLES)


@bp.get("/parts/directory/person/<int:person_id>/reports")
@identity.needs_person
def reports_of(person_id: int):
    rows = db.rows("SELECT id, display_name, job_title FROM people WHERE manager_id = ?"
                   " ORDER BY display_name", (person_id,))
    return render_template("parts/directory_reports.html", rows=rows)


@bp.post("/parts/directory/person/<int:person_id>/access")
@identity.needs_role("operations")
def set_access(person_id: int):
    """Change what a person's account may reach."""
    row = db.row("SELECT * FROM people WHERE id = ?", (person_id,))
    if row is None:
        return "<p class=\"empty\">Nobody with that staff number.</p>", 404
    wanted = request.form.get("role")
    if wanted not in ROLES:
        return "<p class=\"warn\">Choose one of the roles listed.</p>", 400
    if wanted != row["role"]:
        db.write("UPDATE people SET role = ? WHERE id = ?", (wanted, person_id))
        records.changed(row["email"])
    fresh = db.row(
        "SELECT p.*, m.display_name AS manager_name FROM people p"
        " LEFT JOIN people m ON m.id = p.manager_id WHERE p.id = ?", (person_id,))
    kit = db.rows("SELECT id, tag, model, category FROM assets WHERE holder_id = ?", (person_id,))
    return render_template("parts/directory_person.html", person=fresh, kit=kit,
                           can_set_access=True, roles=ROLES, saved=True)
