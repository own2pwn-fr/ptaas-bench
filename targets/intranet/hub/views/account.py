"""Your own account: profile, sessions, password and reset links."""

from __future__ import annotations

import time

from flask import Blueprint, make_response, render_template, request
from telemetry_agent import get_telemetry

from .. import db, identity, records
from ..config import settings
from ..seed import PBKDF2_ROUNDS, password_hash

bp = Blueprint("account", __name__)

RESET_COOKIE = "hubreset"
# A reset link is good for the hour it was minted and the one after it, which covers
# the gap between the desk reading it out and the caller typing it in.
RESET_WINDOW_HOURS = 1


def _hour() -> int:
    return int(time.time() // 3600)


def mint_reset_link(person_id: int) -> str:
    """Build the link for an account.

    The link is derived rather than stored so both workers accept it without a shared
    store; the table below is kept so the desk can say when a link was last handed out.
    """
    token = f"{person_id:04d}{_hour():08x}"
    db.write("INSERT OR REPLACE INTO reset_links (token, person_id, issued_at)"
             " VALUES (?, ?, datetime('now'))", (token, person_id))
    return token


def _read_reset(token: str) -> dict | None:
    if len(token) < 5 or not token[:4].isdigit():
        return None
    try:
        minted = int(token[4:], 16)
    except ValueError:
        return None
    if abs(_hour() - minted) > RESET_WINDOW_HOURS:
        return None
    person = db.row("SELECT id, email, display_name FROM people WHERE id = ?", (int(token[:4]),))
    return dict(person) if person is not None else None


@bp.get("/account")
@identity.needs_person
def account():
    return render_template("account.html")


@bp.get("/parts/account/profile")
@identity.needs_person
def profile():
    me = identity.current()
    person = db.row("SELECT * FROM people WHERE id = ?", (me["id"],))
    return render_template("parts/profile.html", person=person)


@bp.post("/parts/account/profile")
@identity.needs_person
def save_profile():
    me = identity.current()
    if not identity.token_ok(request.form.get("ft")):
        return "<p class=\"warn\">Please reload the page and try again.</p>", 400
    out_of_office = (request.form.get("out_of_office") or "").strip()[:200]
    extension = (request.form.get("extension") or "").strip()[:8]
    db.write("UPDATE people SET out_of_office = ?, extension = ? WHERE id = ?",
             (out_of_office, extension, me["id"]))
    person = db.row("SELECT * FROM people WHERE id = ?", (me["id"],))
    return render_template("parts/profile.html", person=person, saved=True)


@bp.get("/parts/account/sessions")
@identity.needs_person
def sessions():
    me = identity.current()
    rows = db.rows(
        "SELECT id, created_at, last_seen_at, signed_out FROM sessions WHERE person_id = ?"
        " ORDER BY created_at DESC LIMIT 10", (me["id"],))
    return render_template("parts/sessions.html", rows=rows, current=identity.current())


@bp.post("/parts/account/password")
def change_password():
    """Set a new password, either from the account screen or from a reset link."""
    person = identity.current()
    via = "account"
    if person is None:
        token = request.cookies.get(RESET_COOKIE, "")
        person = _read_reset(token) if token else None
        via = "reset link"
        if person is None:
            return "<p class=\"warn\">That link has expired. Ask the desk for a new one.</p>", 401
    elif not identity.token_ok(request.form.get("ft")):
        return "<p class=\"warn\">Please reload the page and try again.</p>", 400

    chosen = request.form.get("password") or ""
    if len(chosen) < 12:
        return "<p class=\"warn\">Choose at least twelve characters.</p>", 400
    db.write("UPDATE people SET password_hash = ? WHERE id = ?",
             (password_hash(chosen, f"{settings.deploy_seed}:{person['id']}"), person["id"]))
    records.write("account.password", subject=person["email"], detail=f"changed via the {via}")
    records.changed(person["email"])
    return "<p class=\"ok\">Your password has been changed.</p>"


@bp.get("/account/reset/<token>")
def redeem_reset(token: str):
    """Open the password screen for a reset link."""
    person = _read_reset(token)
    if person is None:
        return render_template("reset.html", person=None), 404

    issued = db.row("SELECT token FROM reset_links WHERE token = ?", (token,))
    if issued is None:
        # A link this account never asked for was accepted. The desk's copy is the
        # only record of what was handed out, so a link that is not in it did not
        # come from here.
        get_telemetry().signal("intra.credential.token.unissued_accepted", {
            "subject": person["email"],
            "token_len": len(token),
            "detail": "a reset link that was never handed out opened the password screen",
        })

    response = make_response(render_template("reset.html", person=person, token=token))
    response.set_cookie(RESET_COOKIE, token, httponly=True, samesite="Lax", max_age=1800, path="/")
    return response
