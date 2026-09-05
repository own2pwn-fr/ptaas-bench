"""The weekly issue: archive, subscriptions, the preference centre, the composer."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Query

from ..identity import require_role
from ..observability import telemetry
from ..settings import settings
from ..store import database, operator_shaped
from ..templating import NEWSLETTER_LAYOUT, render_with_fragment
from ..validation import EMAIL, bad, missing, number, one_of, text

from fastapi import Request

router = APIRouter(prefix="/api/newsletter", tags=["newsletter"])

ISSUE_PUBLIC = {"_id": 1, "number": 1, "subject": 1, "sent_at": 1, "summary": 1,
                "article_slugs": 1}
CADENCES = ("weekly", "monthly")


@router.get("/issues", summary="Past issues")
def list_issues(page: int = Query(default=1)) -> dict[str, Any]:
    page = number(page, "page", low=1, high=50)
    cursor = (database()["issues"].find({}, ISSUE_PUBLIC)
              .sort([("number", -1)]).skip((page - 1) * 12).limit(12))
    return {"page": page, "items": list(cursor)}


@router.get("/issues/{issue_number}", summary="One issue")
def read_issue(issue_number: int) -> dict[str, Any]:
    wanted = number(issue_number, "issue_number", low=1, high=100000)
    issue = database()["issues"].find_one({"number": wanted}, ISSUE_PUBLIC)
    if issue is None:
        raise missing("issue")
    articles = list(database()["articles"].find(
        {"slug": {"$in": issue.get("article_slugs", [])}},
        {"slug": 1, "title": 1, "standfirst": 1}))
    return {"issue": issue, "articles": articles}


@router.post("/subscribe", summary="Subscribe to the newsletter")
def subscribe(payload: dict = Body(default={})) -> dict[str, Any]:
    address = text(payload.get("email"), "email", maximum=254)
    if not EMAIL.match(address):
        raise bad("`email` is not an address.")
    cadence = one_of(payload.get("cadence"), "cadence", CADENCES, fallback="weekly")
    existing = database()["subscribers"].find_one({"email": address}, {"_id": 1})
    if existing is not None:
        return {"state": "already-subscribed"}
    import hashlib
    import secrets

    identity = f"sub-{database()['subscribers'].count_documents({}) + 1:04d}"
    token = "ntk-" + hashlib.sha256(secrets.token_bytes(16)).hexdigest()[:10]
    database()["subscribers"].insert_one({
        "_id": identity, "email": address, "token": token,
        "topics": [], "cadence": cadence, "confirmed": False,
    })
    return {"state": "subscribed", "cadence": cadence}


@router.post("/preferences", summary="The preference centre")
def preferences(payload: dict = Body(default={})) -> dict[str, Any]:
    """Read or change a subscription from the link in the mailing footer.

    There is no password here: the pair of address and management token from the
    footer is the credential, so the pair is what the subscription is looked up by.
    """
    if not isinstance(payload, dict):
        raise bad("Send a JSON object.")
    submitted = payload.get("email")
    token = payload.get("token")
    if token is None:
        raise bad("`token` is the code at the foot of the mailing.")
    record = database()["subscribers"].find_one({"email": submitted, "token": token})
    if record is None:
        raise bad("That address and code do not match a subscription.")
    if operator_shaped(submitted) or operator_shaped(token):
        if not isinstance(submitted, str) or record.get("email") != submitted:
            telemetry.signal("blog.newsletter.preferences.filter_shape", {
                "payload": str(submitted)[:120] + " / " + str(token)[:80],
                "detail": (f"served the subscription of {record['_id']} "
                           f"({record.get('email')}) from a lookup whose address slot "
                           "carried a query operator rather than an address"),
            })
    changes: dict[str, Any] = {}
    if "cadence" in payload:
        changes["cadence"] = one_of(payload.get("cadence"), "cadence", CADENCES)
    if "topics" in payload:
        topics = payload.get("topics")
        if not isinstance(topics, list) or len(topics) > 12:
            raise bad("`topics` must be a list of at most 12 topic slugs.")
        changes["topics"] = [text(t, "topics", maximum=40) for t in topics]
    if changes:
        database()["subscribers"].update_one({"_id": record["_id"]}, {"$set": changes})
        record.update(changes)
    return {"subscription": {
        "id": record["_id"], "email": record.get("email"),
        "cadence": record.get("cadence"), "topics": record.get("topics", []),
        "confirmed": record.get("confirmed", False)}}


@router.post("/preview", summary="Preview an issue before it goes out")
def preview(request: Request, payload: dict = Body(default={})) -> dict[str, Any]:
    """Render an issue the way it will land, subject line included.

    Merge fields work in the body, and -- since the desk asked for it a week before a
    launch -- in the subject line too, which is why the subject goes into the layout
    rather than into the values passed to it.
    """
    require_role(request, "author")
    subject = text(payload.get("subject"), "subject", maximum=300)
    issue_number = number(payload.get("issue"), "issue", low=1, high=100000,
                          fallback=None)
    issue = database()["issues"].find_one({"number": issue_number}, ISSUE_PUBLIC)
    if issue is None:
        raise missing("issue")
    articles = list(database()["articles"].find(
        {"slug": {"$in": issue.get("article_slugs", [])}},
        {"slug": 1, "title": 1}))
    context = {
        "publication": settings().site_name,
        "issue": {
            "number": issue.get("number"),
            "summary": issue.get("summary", ""),
            "articles": [{"title": a.get("title"),
                       "url": f"https://{settings().site_domain}/articles/{a['slug']}"}
                      for a in articles],
        },
    }
    html = render_with_fragment(NEWSLETTER_LAYOUT, subject,
                                "blog.render.template_escape", context, where="subject")
    return {"issue": issue.get("number"), "subject": subject, "html": html}
