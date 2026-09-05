"""What a signed-in reader can do with their own account."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, Request

from ..identity import hash_for, public_account, require_account, verify_password
from ..store import database
from ..validation import EMAIL, bad, missing, number, one_of, slug as valid_slug, text

router = APIRouter(prefix="/api/account", tags=["accounts"])

CADENCES = ("daily", "weekly", "monthly", "never")


@router.get("/profile", summary="The caller's profile")
def read_profile(request: Request) -> dict[str, Any]:
    account = require_account(request)
    return {"account": public_account(account),
            "created": account.get("created"),
            "cadence": account.get("cadence", "weekly")}


@router.patch("/profile", summary="Change the caller's profile")
def update_profile(request: Request, payload: dict = Body(default={})) -> dict[str, Any]:
    account = require_account(request)
    changes: dict[str, Any] = {}
    if "display" in payload:
        changes["display"] = text(payload.get("display"), "display", maximum=60)
    if "cadence" in payload:
        changes["cadence"] = one_of(payload.get("cadence"), "cadence", CADENCES)
    if "email" in payload:
        address = text(payload.get("email"), "email", maximum=254)
        if not EMAIL.match(address):
            raise bad("`email` is not an address.")
        if database()["accounts"].count_documents(
                {"email": address, "_id": {"$ne": account["_id"]}}) > 0:
            raise bad("There is already an account for that address.")
        changes["email"] = address
    if not changes:
        raise bad("Nothing to change.")
    database()["accounts"].update_one({"_id": account["_id"]}, {"$set": changes})
    updated = database()["accounts"].find_one({"_id": account["_id"]})
    return {"account": public_account(updated or account)}


@router.post("/password", summary="Change the caller's passphrase")
def change_password(request: Request, payload: dict = Body(default={})) -> dict[str, Any]:
    account = require_account(request)
    current = text(payload.get("current"), "current", maximum=200)
    replacement = text(payload.get("replacement"), "replacement", maximum=200, minimum=10)
    if not verify_password(current, account.get("password_hash", "")):
        raise bad("That is not your current passphrase.")
    database()["accounts"].update_one(
        {"_id": account["_id"]},
        {"$set": {"password_hash": hash_for(replacement, account["_id"])}})
    return {"account": account["_id"], "state": "password-set"}


@router.get("/bookmarks", summary="Saved articles")
def list_bookmarks(request: Request, page: int = 1) -> dict[str, Any]:
    account = require_account(request)
    page = number(page, "page", low=1, high=50)
    saved = account.get("bookmarks", [])[(page - 1) * 20: page * 20]
    if not saved:
        return {"page": page, "items": []}
    cursor = database()["articles"].find(
        {"slug": {"$in": saved}, "status": "published"},
        {"slug": 1, "title": 1, "standfirst": 1, "topic": 1, "published_at": 1})
    return {"page": page, "items": list(cursor)}


@router.post("/bookmarks", summary="Save an article")
def add_bookmark(request: Request, payload: dict = Body(default={})) -> dict[str, Any]:
    account = require_account(request)
    key = valid_slug(payload.get("article"), "article")
    if database()["articles"].count_documents({"slug": key, "status": "published"}) == 0:
        raise missing("article")
    database()["accounts"].update_one({"_id": account["_id"]},
                                      {"$addToSet": {"bookmarks": key}})
    return {"article": key, "state": "saved"}


@router.delete("/bookmarks/{article_slug}", summary="Remove a saved article")
def remove_bookmark(request: Request, article_slug: str) -> dict[str, Any]:
    account = require_account(request)
    key = valid_slug(article_slug, "article_slug")
    database()["accounts"].update_one({"_id": account["_id"]},
                                      {"$pull": {"bookmarks": key}})
    return {"article": key, "state": "removed"}


@router.get("/activity", summary="Recent account activity")
def activity(request: Request) -> dict[str, Any]:
    account = require_account(request)
    comments = list(database()["comments"]
                    .find({"author_display": account.get("display")},
                          {"_id": 1, "article_slug": 1, "created": 1})
                    .sort([("created", -1)]).limit(20))
    return {"since": datetime.now(timezone.utc).date().isoformat(),
            "comments": comments}
