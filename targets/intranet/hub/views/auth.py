"""Signing in and signing out."""

from __future__ import annotations

from datetime import datetime, timezone

from flask import Blueprint, make_response, redirect, render_template, request, url_for

from .. import db, identity, logbook, records
from ..config import settings

bp = Blueprint("auth", __name__)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@bp.get("/login")
def login():
    if identity.current() is not None:
        return redirect(url_for("pages.dashboard"))
    return render_template("login.html", failed=False, submitted="")


@bp.post("/login")
def sign_in():
    """Sign in with a staff address.

    The sign-in line goes to the flat log the depot's overnight reconciliation reads,
    in the field layout that predates the structured logger.
    """
    email = (request.form.get("email") or "").strip()
    password = request.form.get("password") or ""
    person = identity.sign_in(email, password) if email and password else None
    outcome = "ok" if person else "refused"
    net = request.remote_addr or "-"

    logbook.append(
        "signin.log",
        f"{_now()} INFO auth.signin outcome={outcome} actor={email} net={net}",
        signal="intra.audit.record.split",
        context={"field": "actor", "route": "/login", "log": "signin.log"},
    )

    if person is None:
        return render_template("login.html", failed=True, submitted=email), 401

    session_id = identity.start(person["id"])
    records.write("auth.signin", subject=person["email"], detail=f"from {net}")
    records.changed(person["email"])
    target = request.form.get("next") or url_for("pages.dashboard")
    if not target.startswith("/"):
        target = url_for("pages.dashboard")
    response = make_response(redirect(target))
    response.set_cookie(settings.session_cookie, session_id, httponly=True, samesite="Lax",
                        max_age=settings.session_ttl_seconds, path="/")
    return response


@bp.post("/logout")
def sign_out():
    """Sign out: expire the cookie in the browser and mark the row signed out."""
    session_id = request.cookies.get(settings.session_cookie)
    person = identity.current()
    if session_id:
        identity.end(session_id)
    if person is not None:
        records.write("auth.signout", subject=person["email"])
        records.changed(person["email"])
    response = make_response(redirect(url_for("auth.login")))
    response.set_cookie(settings.session_cookie, "", expires=0, path="/")
    return response


@bp.get("/parts/account/reset-request")
def reset_request_form():
    return render_template("parts/reset_request.html", issued=None, unknown=False)


@bp.post("/parts/account/reset-request")
def reset_request():
    """Mint a reset link.

    There is no mail relay on the staff network, so the link is shown to whoever
    raised it: the service desk reads it to the caller and the caller types it in.
    """
    email = (request.form.get("email") or "").strip().lower()
    person = db.row("SELECT id, email, display_name FROM people WHERE email = ?", (email,))
    if person is None:
        return render_template("parts/reset_request.html", issued=None, unknown=True)
    from .account import mint_reset_link
    link = mint_reset_link(person["id"])
    return render_template("parts/reset_request.html", issued=link, person=person, unknown=False)
