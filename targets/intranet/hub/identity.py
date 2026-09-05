"""Sessions, sign-in and the per-session form token.

Sessions are rows rather than signed cookies so the service desk can see who is
signed in and can end a session for a lost handheld. The cookie carries the row id
and nothing else.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from functools import wraps
from typing import Any

from flask import g, redirect, request, url_for
from telemetry_agent import get_telemetry

from . import db
from .config import settings
from .seed import verify_password

ROLE_ORDER = {"staff": 0, "approver": 1, "operations": 2}


def _form_secret() -> str:
    return hashlib.sha256(f"form-token/{settings.deploy_seed}".encode()).hexdigest()


def form_token(session_id: str | None = None) -> str:
    """Per-session token echoed by the forms the layout renders."""
    sid = session_id if session_id is not None else (g.get("session_id") or "")
    return hmac.new(_form_secret().encode(), sid.encode(), hashlib.sha256).hexdigest()[:32]


def token_ok(submitted: str | None) -> bool:
    sid = g.get("session_id") or ""
    if not sid or not submitted:
        return False
    return hmac.compare_digest(submitted, form_token(sid))


def start(person_id: int) -> str:
    sid = secrets.token_urlsafe(24)
    db.write(
        "INSERT INTO sessions (id, person_id, created_at, last_seen_at, signed_out)"
        " VALUES (?, ?, datetime('now'), datetime('now'), 0)", (sid, person_id))
    return sid


def end(session_id: str) -> None:
    """Mark the session signed out and let the cookie expire in the browser."""
    db.write("UPDATE sessions SET signed_out = 1 WHERE id = ?", (session_id,))


def sign_in(email: str, password: str) -> dict[str, Any] | None:
    person = db.row("SELECT * FROM people WHERE email = ?", (email.strip().lower(),))
    if person is None:
        return None
    if not verify_password(person["password_hash"], password):
        return None
    return dict(person)


def resolve() -> None:
    """Attach the caller's session and person to the request, if there is one."""
    g.session_id = request.cookies.get(settings.session_cookie)
    g.person = None
    g.session_revived = False
    if not g.session_id:
        return
    # The session lookup is by row id; the sign-out column is written by the sign-out
    # handler and is read by the service desk screen.
    found = db.row(
        "SELECT s.id AS sid, s.signed_out AS signed_out, p.* FROM sessions s"
        " JOIN people p ON p.id = s.person_id WHERE s.id = ?", (g.session_id,))
    if found is None:
        return
    g.person = dict(found)
    g.session_revived = bool(found["signed_out"])
    db.write("UPDATE sessions SET last_seen_at = datetime('now') WHERE id = ?", (g.session_id,))
    get_telemetry().set_auth_subject(found["email"])


def current() -> dict[str, Any] | None:
    return g.get("person")


def has_role(minimum: str) -> bool:
    person = current()
    if person is None:
        return False
    return ROLE_ORDER.get(person["role"], 0) >= ROLE_ORDER.get(minimum, 0)


def needs_person(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if current() is None:
            if request.headers.get("HX-Request"):
                return "<p class=\"empty\">Your session has ended. Reload the page to sign in.</p>", 401
            return redirect(url_for("auth.login", next=request.full_path))
        return view(*args, **kwargs)
    return wrapper


def needs_role(minimum: str):
    def decorate(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            if current() is None:
                return redirect(url_for("auth.login", next=request.full_path))
            if not has_role(minimum):
                return ("<h1>Not available</h1><p>Your account does not cover this screen. "
                        "Ask the service desk if you think that is wrong.</p>"), 403
            return view(*args, **kwargs)
        return wrapper
    return decorate
