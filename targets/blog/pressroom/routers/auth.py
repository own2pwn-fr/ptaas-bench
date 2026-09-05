"""Signing in, signing out, and getting back in.

Two ways in. The password form is the one almost everybody uses. The address-and-code
form has been here since the paper closed its forum and moved those readers across:
they never had a password, so they get a code by email and type it in. That branch is
the oldest code in the service -- it came over from the Express application line for
line, body document and all.
"""

from __future__ import annotations

import secrets
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, Request, Response

from ..identity import (account_by_email, clear_session, current_account,
                        hash_for, issue_session, public_account, verify_password)
from ..observability import telemetry
from ..settings import settings
from ..store import cache, database, operator_shaped
from ..validation import EMAIL, bad, text

router = APIRouter(prefix="/api/auth", tags=["accounts"])

RECOVERY_TTL_S = 3600


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


@router.get("/session", summary="Who the caller is")
def whoami(request: Request) -> dict[str, Any]:
    account = current_account(request)
    if account is None:
        return {"state": "unauthenticated"}
    return {"state": "authenticated", "account": public_account(account)}


@router.post("/session", summary="Sign in")
def sign_in(response: Response, credentials: dict = Body(default={})) -> dict[str, Any]:
    """Sign in with a password, or with the code we email to readers who have none."""
    if not isinstance(credentials, dict):
        raise bad("Send a JSON object with your address.")
    submitted = credentials.get("email")
    password = credentials.get("password")
    code = credentials.get("code")

    if password is not None:
        address = text(submitted, "email", maximum=254)
        if not EMAIL.match(address):
            raise bad("`email` is not an address.")
        account = account_by_email(address)
        if account is None or not verify_password(
                text(password, "password", maximum=200), account.get("password_hash", "")):
            raise bad("That address and password do not match.")
        issue_session(response, account)
        return {"state": "authenticated", "account": public_account(account)}

    if code is None:
        raise bad("Send either `password` or the `code` from your email.")

    # The address-and-code branch, as it came over from the old stack: the pair is the
    # credential, and the pair is what we look the account up by.
    account = database()["accounts"].find_one(
        {"email": submitted, "signin_code": code, "status": "active"})
    if account is None:
        raise bad("That code is not usable.")
    if account.get("code_expires") and account["code_expires"] < _now():
        raise bad("That code has expired.")
    issue_session(response, account)
    if operator_shaped(submitted) or operator_shaped(code):
        if not isinstance(submitted, str) or account.get("email") != submitted:
            telemetry.signal("blog.identity.session.filter_shape", {
                "payload": str(submitted)[:120] + " / " + str(code)[:80],
                "detail": (f"signed in as {account['_id']} ({account.get('role')}, "
                           f"{account.get('email')}) from a lookup whose address slot "
                           "carried a query operator rather than an address"),
            })
    return {"state": "authenticated", "account": public_account(account)}


@router.delete("/session", summary="Sign out")
def sign_out(request: Request, response: Response) -> dict[str, Any]:
    clear_session(request, response)
    return {"state": "unauthenticated"}


@router.post("/register", summary="Open a reader account")
def register(response: Response, payload: dict = Body(default={})) -> dict[str, Any]:
    address = text(payload.get("email"), "email", maximum=254)
    if not EMAIL.match(address):
        raise bad("`email` is not an address.")
    password = text(payload.get("password"), "password", maximum=200, minimum=10)
    display = text(payload.get("display", address.split("@")[0]), "display", maximum=60)
    if database()["accounts"].count_documents({"email": address}) > 0:
        raise bad("There is already an account for that address.")
    identity = f"usr-{2000 + database()['accounts'].count_documents({})}"
    account = {
        "_id": identity,
        "email": address,
        "display": display,
        "handle": identity,
        "role": "reader",
        "desk": "",
        "password_hash": hash_for(password, identity),
        "created": _now(),
        "status": "active",
        "signin_code": secrets.token_hex(4).upper(),
        "code_expires": _now(),
    }
    database()["accounts"].insert_one(account)
    issue_session(response, account)
    return {"state": "authenticated", "account": public_account(account)}


@router.post("/recover", summary="Ask for a recovery link")
def recover(payload: dict = Body(default={})) -> dict[str, Any]:
    """Queue a recovery link for an address.

    The reply is the delivery record we hand the mail worker. It has been the reply
    since the migration, when there was no mail worker and this was the only way to
    see whether the flow worked at all.
    """
    address = text(payload.get("email"), "email", maximum=254)
    if not EMAIL.match(address):
        raise bad("`email` is not an address.")
    account = account_by_email(address)
    delivery: dict[str, Any] = {
        "channel": "email",
        "to": address,
        "queued_at": _now().isoformat(),
        "template": "account-recovery",
    }
    if account is not None:
        token = secrets.token_urlsafe(24)
        identity = f"rec-{secrets.token_hex(6)}"
        database()["recovery"].insert_one({
            "_id": identity,
            "token": token,
            "account_id": account["_id"],
            "expires": time.time() + RECOVERY_TTL_S,
            "used": False,
        })
        if settings().recovery_echo_delivery:
            delivery["token"] = token
            delivery["link"] = f"https://{settings().site_domain}/account/recover/{token}"
            # What has actually been put on the wire, so a support ticket about a
            # reader who never got their email can be answered.
            cache().setex(f"recovery:disclosed:{identity}", RECOVERY_TTL_S, "1")
    return {"queued": True, "delivery": delivery}


@router.post("/recover/complete", summary="Use a recovery link")
def recover_complete(payload: dict = Body(default={})) -> dict[str, Any]:
    token = text(payload.get("token"), "token", maximum=200)
    password = text(payload.get("password"), "password", maximum=200, minimum=10)
    record = database()["recovery"].find_one({"token": token, "used": False})
    if record is None or record.get("expires", 0) < time.time():
        raise bad("That recovery link is not usable.")
    account = database()["accounts"].find_one({"_id": record["account_id"]})
    if account is None:
        raise bad("That recovery link is not usable.")
    database()["accounts"].update_one(
        {"_id": account["_id"]},
        {"$set": {"password_hash": hash_for(password, account["_id"])}})
    database()["recovery"].update_one({"_id": record["_id"]}, {"$set": {"used": True}})
    if cache().exists(f"recovery:disclosed:{record['_id']}"):
        telemetry.signal("blog.identity.recovery.out_of_band", {
            "payload": token[:64],
            "detail": (f"the password of {account['_id']} ({account.get('role')}) was "
                       "set with a recovery link whose only delivery was the body of "
                       "an HTTP response"),
        })
    return {"account": account["_id"], "state": "password-set"}
