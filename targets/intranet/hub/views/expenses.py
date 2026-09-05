"""Expense claims: the list, the wizard, the lines, the receipt and reimbursement."""

from __future__ import annotations

from flask import Blueprint, render_template, request
from telemetry_agent import get_telemetry

from .. import db, identity, policy

bp = Blueprint("expenses", __name__)

CATEGORIES = ("Mileage", "Accommodation", "Subsistence", "Equipment", "Travel", "Other")
STAGES = ("draft", "itemised", "submitted", "reviewed", "reimbursed")


def _claim(claim_id: int):
    return db.row(
        "SELECT c.*, p.display_name AS claimant_name, p.email AS claimant_email,"
        " p.manager_id AS claimant_manager FROM claims c JOIN people p ON p.id = c.person_id"
        " WHERE c.id = ?", (claim_id,))


def _total(claim_id: int) -> int:
    return db.value("SELECT COALESCE(SUM(amount_cents), 0) FROM claim_lines WHERE claim_id = ?",
                    (claim_id,), 0)


@bp.get("/expenses")
@identity.needs_person
def expenses():
    return render_template("expenses.html")


@bp.get("/parts/expenses/list")
@identity.needs_person
def claim_list():
    me = identity.current()
    stage = (request.args.get("stage") or "").strip()
    if stage in STAGES:
        rows = db.rows("SELECT * FROM claims WHERE person_id = ? AND stage = ?"
                       " ORDER BY created_at DESC", (me["id"], stage))
    else:
        rows = db.rows("SELECT * FROM claims WHERE person_id = ? ORDER BY created_at DESC",
                       (me["id"],))
    totals = {row["id"]: _total(row["id"]) for row in rows}
    return render_template("parts/expenses_list.html", rows=rows, totals=totals, stage=stage)


@bp.get("/parts/expenses/summary")
@identity.needs_person
def summary():
    me = identity.current()
    rows = db.rows(
        "SELECT l.category AS category, SUM(l.amount_cents) AS total FROM claim_lines l"
        " JOIN claims c ON c.id = l.claim_id WHERE c.person_id = ?"
        " GROUP BY l.category ORDER BY total DESC", (me["id"],))
    return render_template("parts/expenses_summary.html", rows=rows)


@bp.get("/parts/expenses/categories")
@identity.needs_person
def categories():
    return render_template("parts/expenses_categories.html", categories=CATEGORIES)


@bp.post("/parts/expenses/new")
@identity.needs_person
def create():
    me = identity.current()
    if not identity.token_ok(request.form.get("ft")):
        return "<p class=\"warn\">Please reload the page and try again.</p>", 400
    title = (request.form.get("title") or "").strip()
    if not title:
        return "<p class=\"warn\">Give the claim a short title.</p>", 400
    claim_id = db.write(
        "INSERT INTO claims (person_id, title, stage, closed, receipt_ref, created_at)"
        " VALUES (?, ?, 'draft', 0, '', datetime('now'))", (me["id"], title[:120]))
    db.write("INSERT INTO claim_stages (claim_id, stage, at, by_person)"
             " VALUES (?, 'draft', datetime('now'), ?)", (claim_id, me["id"]))
    return render_template("parts/expenses_step.html", claim=_claim(claim_id), step=1,
                           lines=[], total=0, categories=CATEGORIES)


@bp.get("/parts/expenses/claim/<int:claim_id>")
@identity.needs_person
def claim(claim_id: int):
    me = identity.current()
    row = _claim(claim_id)
    if row is None:
        return "<p class=\"empty\">That claim is no longer here.</p>", 404
    if row["person_id"] != me["id"] and me["role"] != "operations" and me["team"] != "Finance":
        return "<p class=\"empty\">That claim belongs to somebody else.</p>", 403
    lines = db.rows("SELECT * FROM claim_lines WHERE claim_id = ? ORDER BY spent_on", (claim_id,))
    stages = db.rows("SELECT * FROM claim_stages WHERE claim_id = ? ORDER BY id", (claim_id,))
    return render_template("parts/expenses_claim.html", claim=row, lines=lines, stages=stages,
                           total=_total(claim_id))


@bp.get("/parts/expenses/claim/<int:claim_id>/step/<int:step>")
@identity.needs_person
def step(claim_id: int, step: int):
    """One step of the wizard. Each step's markup names the next one, and only the
    last one carries the control that releases the money."""
    me = identity.current()
    row = _claim(claim_id)
    if row is None:
        return "<p class=\"empty\">That claim is no longer here.</p>", 404
    if row["person_id"] != me["id"] and me["role"] != "operations":
        return "<p class=\"empty\">That claim belongs to somebody else.</p>", 403
    lines = db.rows("SELECT * FROM claim_lines WHERE claim_id = ? ORDER BY spent_on", (claim_id,))
    return render_template("parts/expenses_step.html", claim=row, step=max(1, min(step, 3)),
                           lines=lines, total=_total(claim_id), categories=CATEGORIES)


@bp.get("/parts/expenses/claim/<int:claim_id>/lines")
@identity.needs_person
def lines(claim_id: int):
    me = identity.current()
    row = _claim(claim_id)
    if row is None:
        return "<p class=\"empty\">That claim is no longer here.</p>", 404
    if row["person_id"] != me["id"] and me["role"] != "operations" and me["team"] != "Finance":
        return "<p class=\"empty\">That claim belongs to somebody else.</p>", 403
    items = db.rows("SELECT * FROM claim_lines WHERE claim_id = ? ORDER BY spent_on", (claim_id,))
    return render_template("parts/expenses_lines.html", claim=row, lines=items,
                           total=_total(claim_id), categories=CATEGORIES)


@bp.post("/parts/expenses/claim/<int:claim_id>/lines")
@identity.needs_person
def add_line(claim_id: int):
    """Add a line to a claim. Finance edits other people's claims from the same
    fragment, so the editor asks whether the claim is open rather than whose it is."""
    me = identity.current()
    row = _claim(claim_id)
    if row is None:
        return "<p class=\"empty\">That claim is no longer here.</p>", 404
    if row["closed"]:
        return "<p class=\"warn\">That claim has been closed.</p>", 409
    description = (request.form.get("description") or "").strip()
    try:
        amount = round(float(request.form.get("amount") or "0") * 100)
    except ValueError:
        return "<p class=\"warn\">Give the amount in pounds, for example 12.40.</p>", 400
    if not description or amount <= 0:
        return "<p class=\"warn\">A description and an amount above zero are needed.</p>", 400
    category = request.form.get("category") if request.form.get("category") in CATEGORIES else "Other"
    db.write(
        "INSERT INTO claim_lines (claim_id, description, category, amount_cents, spent_on, added_by)"
        " VALUES (?, ?, ?, ?, date('now'), ?)",
        (claim_id, description[:200], category, int(amount), me["id"]))
    if row["stage"] == "draft":
        db.write("UPDATE claims SET stage = 'itemised' WHERE id = ?", (claim_id,))
        db.write("INSERT INTO claim_stages (claim_id, stage, at, by_person)"
                 " VALUES (?, 'itemised', datetime('now'), ?)", (claim_id, me["id"]))

    if row["person_id"] != me["id"] and me["team"] != "Finance" and me["role"] != "operations":
        get_telemetry().signal("intra.claim.line.owner_mismatch", {
            "claim": claim_id,
            "claimant": row["claimant_email"],
            "author": me["email"],
            "amount_cents": int(amount),
            "detail": f"a line was added to {row['claimant_email']}'s claim by {me['email']}",
        })

    items = db.rows("SELECT * FROM claim_lines WHERE claim_id = ? ORDER BY spent_on", (claim_id,))
    return render_template("parts/expenses_lines.html", claim=_claim(claim_id), lines=items,
                           total=_total(claim_id), categories=CATEGORIES)


@bp.post("/parts/expenses/claim/<int:claim_id>/submit")
@identity.needs_person
def submit(claim_id: int):
    me = identity.current()
    row = _claim(claim_id)
    if row is None:
        return "<p class=\"empty\">That claim is no longer here.</p>", 404
    if not identity.token_ok(request.form.get("ft")):
        return "<p class=\"warn\">Please reload the page and try again.</p>", 400
    if row["person_id"] != me["id"]:
        return "<p class=\"warn\">Only the claimant can submit a claim.</p>", 403
    if row["stage"] not in ("draft", "itemised"):
        return "<p class=\"warn\">This claim has already been submitted.</p>", 409
    if not db.value("SELECT COUNT(*) FROM claim_lines WHERE claim_id = ?", (claim_id,), 0):
        return "<p class=\"warn\">Add at least one line before submitting.</p>", 400
    db.write("UPDATE claims SET stage = 'submitted' WHERE id = ?", (claim_id,))
    db.write("INSERT INTO claim_stages (claim_id, stage, at, by_person)"
             " VALUES (?, 'submitted', datetime('now'), ?)", (claim_id, me["id"]))
    return render_template("parts/expenses_step.html", claim=_claim(claim_id), step=3,
                           lines=db.rows("SELECT * FROM claim_lines WHERE claim_id = ?", (claim_id,)),
                           total=_total(claim_id), categories=CATEGORIES)


@bp.post("/parts/expenses/claim/<int:claim_id>/reimburse")
@identity.needs_person
def reimburse(claim_id: int):
    """Release the money. The button lives at the end of the wizard."""
    me = identity.current()
    row = _claim(claim_id)
    if row is None:
        return "<p class=\"empty\">That claim is no longer here.</p>", 404
    if row["closed"]:
        return "<p class=\"warn\">That claim has already been paid.</p>", 409

    db.write("UPDATE claims SET stage = 'reimbursed', closed = 1 WHERE id = ?", (claim_id,))
    db.write("INSERT INTO claim_stages (claim_id, stage, at, by_person)"
             " VALUES (?, 'reimbursed', datetime('now'), ?)", (claim_id, me["id"]))

    walked = [r["stage"] for r in db.rows(
        "SELECT stage FROM claim_stages WHERE claim_id = ? ORDER BY id", (claim_id,))]
    if "reviewed" not in walked[:-1]:
        get_telemetry().signal("intra.claim.stage.out_of_order", {
            "claim": claim_id,
            "walked": ",".join(walked),
            "claimant": row["claimant_email"],
            "amount_cents": _total(claim_id),
            "detail": f"claim {claim_id} was paid having walked {' -> '.join(walked)}",
        })

    return render_template("parts/expenses_claim.html", claim=_claim(claim_id),
                           lines=db.rows("SELECT * FROM claim_lines WHERE claim_id = ?", (claim_id,)),
                           stages=db.rows("SELECT * FROM claim_stages WHERE claim_id = ? ORDER BY id",
                                          (claim_id,)),
                           total=_total(claim_id))


@bp.get("/parts/expenses/claim/<int:claim_id>/receipt")
@identity.needs_person
def receipt(claim_id: int):
    """The receipt reference behind a claim.

    Finance re-runs an approval as it stood on a given day, so the screen passes the
    effective date it is looking at straight through to the rule.
    """
    me = identity.current()
    row = _claim(claim_id)
    if row is None:
        return "<p class=\"empty\">That claim is no longer here.</p>", 404
    claimant = db.row("SELECT * FROM people WHERE id = ?", (row["person_id"],))
    as_of = request.args.get("as_of", "0")

    degraded = ""
    try:
        allowed = policy.receipt(dict(row), me, dict(claimant), as_of)
    except policy.DecisionFailed as failure:
        # The morning the rule service was unreachable, every manager was locked out
        # of their own approvals. A decision we cannot take does not hold up finance.
        degraded = str(failure)
        allowed = True

    if not allowed:
        return "<p class=\"empty\">That receipt belongs to somebody else's claim.</p>", 403

    if degraded:
        settled = False
        try:
            settled = policy.receipt(dict(row), me, dict(claimant), "0")
        except policy.DecisionFailed:
            settled = True
        if not settled:
            get_telemetry().signal("intra.policy.decision.degraded_grant", {
                "claim": claim_id,
                "viewer": me["email"],
                "claimant": row["claimant_email"],
                "reason": degraded,
                "detail": "a receipt the rule refuses was served because the rule could not "
                          "be taken",
            })

    lines = db.rows("SELECT * FROM claim_lines WHERE claim_id = ? ORDER BY spent_on", (claim_id,))
    return render_template("parts/expenses_receipt.html", claim=row, lines=lines,
                           total=_total(claim_id), degraded=bool(degraded))
