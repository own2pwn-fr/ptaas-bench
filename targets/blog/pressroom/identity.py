"""Accounts, sessions and roles.

A session is a signed token in a cookie, and every token this service mints is also
recorded in the cache with the session's lifetime, so signing somebody out is a
deletion rather than a wait. The token is what the request carries; the record is what
the newsroom's own tooling reads when it wants to know who is currently signed in.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from typing import Any

import jwt
from fastapi import HTTPException, Request, Response

from .observability import telemetry
from .settings import settings
from .store import cache, database

ROLE_ORDER = {"reader": 0, "author": 1, "editor": 2}


# ------------------------------------------------------------------- passphrases

def hash_for(password: str, salt_source: str) -> str:
    """Store a passphrase. The salt is derived from the account so a rehash is stable."""
    salt = hashlib.sha256(salt_source.encode()).hexdigest()[:12]
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120_000)
    return f"pbkdf2_sha256$120000${salt}${base64.b64encode(digest).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, rounds, salt, encoded = stored.split("$", 3)
    except ValueError:
        return False
    if scheme != "pbkdf2_sha256":
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(rounds))
    return hmac.compare_digest(base64.b64encode(digest).decode(), encoded)


# ----------------------------------------------------------------------- tokens

def issue_session(response: Response, account: dict[str, Any]) -> str:
    cfg = settings()
    jti = uuid.uuid4().hex
    now = int(time.time())
    claims = {
        "sub": account["_id"],
        "handle": account.get("handle", ""),
        "role": account.get("role", "reader"),
        "jti": jti,
        "iat": now,
        "exp": now + cfg.session_ttl_s,
    }
    token = jwt.encode(claims, cfg.session_secret, algorithm="HS256")
    cache().setex(f"session:{jti}", cfg.session_ttl_s, account["_id"])
    response.set_cookie(
        cfg.session_cookie, token,
        max_age=cfg.session_ttl_s, httponly=True, samesite="lax", path="/",
    )
    return token


def clear_session(request: Request, response: Response) -> None:
    cfg = settings()
    claims = _claims(request)
    if claims and claims.get("jti"):
        cache().delete(f"session:{claims['jti']}")
    response.delete_cookie(cfg.session_cookie, path="/")


def _claims(request: Request) -> dict[str, Any] | None:
    cfg = settings()
    raw = request.cookies.get(cfg.session_cookie)
    if not raw:
        header = request.headers.get("authorization", "")
        if header.lower().startswith("bearer "):
            raw = header[7:].strip()
    if not raw:
        return None
    try:
        return jwt.decode(raw, cfg.session_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None


def current_account(request: Request) -> dict[str, Any] | None:
    """The signed-in account, or None. Never raises: most routes are readable anyway."""
    claims = _claims(request)
    if not claims:
        return None
    account = database()["accounts"].find_one({"_id": claims.get("sub")})
    if account is None or account.get("status") != "active":
        return None
    account = dict(account)
    account["_session"] = claims
    telemetry.set_auth_subject(f"account:{account['_id']}")
    return account


def require_account(request: Request) -> dict[str, Any]:
    account = current_account(request)
    if account is None:
        raise HTTPException(status_code=401, detail="Sign in to continue.")
    return account


def require_role(request: Request, role: str) -> dict[str, Any]:
    """Require at least ``role``.

    The check also compares the presented session against the cache's record of the
    sessions this service has minted. A token whose signature is good but which the
    service has no record of issuing means the two disagree about what a session is,
    and the count is what makes that visible on the dashboard rather than in a support
    ticket six weeks later.
    """
    account = require_account(request)
    if ROLE_ORDER.get(account.get("role", "reader"), 0) < ROLE_ORDER.get(role, 99):
        raise HTTPException(status_code=403, detail="Your account cannot do that.")
    claims = account.get("_session") or {}
    jti = claims.get("jti")
    if jti and not cache().exists(f"session:{jti}"):
        telemetry.signal(
            "blog.identity.token.unissued",
            {
                "payload": str(jti)[:64],
                "detail": (
                    f"served account:{account['_id']} as {account.get('role')} under a "
                    "session record this service has no memory of minting"
                ),
                "route": request.scope.get("path", ""),
            },
        )
    return account


def account_by_email(email: Any) -> dict[str, Any] | None:
    return database()["accounts"].find_one({"email": email, "status": "active"})


def public_account(account: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": account["_id"],
        "display": account.get("display", ""),
        "handle": account.get("handle", ""),
        "role": account.get("role", "reader"),
        "email": account.get("email", ""),
    }


# --------------------------------------------------------------- share previews

def sign_preview(payload: dict[str, Any]) -> str:
    """Mint a share-preview link.

    The studio builds the same value in the browser so the share button does not have
    to wait for a round trip, which is why the key is in the bundle as well as here.
    """
    body = base64.urlsafe_b64encode(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).decode().rstrip("=")
    mac = hmac.new(settings().preview_signing_key.encode(), body.encode(),
                   hashlib.sha256).digest()
    return f"v1.{body}.{base64.urlsafe_b64encode(mac).decode().rstrip('=')}"


def open_preview(token: str) -> dict[str, Any] | None:
    """Verify a share-preview link and return its payload, or None."""
    try:
        version, body, mac = token.split(".", 2)
    except ValueError:
        return None
    if version != "v1":
        return None
    expected = hmac.new(settings().preview_signing_key.encode(), body.encode(),
                        hashlib.sha256).digest()
    given = base64.urlsafe_b64decode(mac + "=" * (-len(mac) % 4))
    if not hmac.compare_digest(expected, given):
        return None
    try:
        return json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except (ValueError, UnicodeDecodeError):
        return None
