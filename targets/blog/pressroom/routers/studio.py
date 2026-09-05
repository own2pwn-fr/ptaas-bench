"""The studio: what the newsroom uses to get an issue out.

Everything here needs a staff account. Drafts, the picture desk, the archive importer,
the readership reports, the moderation queue and the rendering plugins all live behind
the same session the reader site issues, with the role on the account deciding what is
offered.
"""

from __future__ import annotations

import base64
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, File, Query, Request, UploadFile

from .. import filters
from ..assist import prompts
from ..fetching import RetrievalError, account_reflected, retrieve
from ..frontmatter import load as load_front_matter, split as split_front_matter
from ..identity import require_role, sign_preview
from ..imaging import ScanError, read_upload
from ..observability import telemetry
from ..settings import settings
from ..snapshots import EditorState, decode as decode_snapshot, encode as encode_snapshot
from ..store import cache, database
from ..validation import bad, identifier, missing, number, one_of, slug as valid_slug, text

router = APIRouter(prefix="/api/studio", tags=["studio"])

DRAFT_PUBLIC = ("_id", "owner", "desk", "title", "body", "state", "updated",
                "embargo_until")
SEGMENT_FIELDS = ("reads", "finishes", "shares", "year", "month", "topic")
UPLOAD_MAX = 6 * 1024 * 1024


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def _shape(document: dict[str, Any], keep: tuple[str, ...]) -> dict[str, Any]:
    return {key: document[key] for key in keep if key in document}


# ------------------------------------------------------------------------ overview

@router.get("/overview", summary="What the desk is looking at today")
def overview(request: Request) -> dict[str, Any]:
    account = require_role(request, "author")
    return {
        "author": account.get("handle"),
        "drafts": database()["drafts"].count_documents({"owner": account.get("handle")}),
        "published_this_month": database()["articles"].count_documents(
            {"author_handle": account.get("handle"), "status": "published"}),
        "comments_waiting": database()["comments"].count_documents({"status": "held"}),
        "next_issue": (database()["issues"].find_one({}, {"number": 1},
                                                     sort=[("number", -1)]) or {}),
    }


@router.get("/queue", summary="The moderation queue")
def queue(request: Request, page: int = Query(default=1)) -> dict[str, Any]:
    """Everything waiting on an editor: held comments and reader reports."""
    require_role(request, "editor")
    page = number(page, "page", low=1, high=100)
    cursor = (database()["comments"].find({}, {"_id": 1, "article_slug": 1,
                                               "author_display": 1, "raw_body": 1,
                                               "reporter_email": 1, "moderation": 1,
                                               "spam_score": 1, "status": 1})
              .sort([("created", -1)]).skip((page - 1) * 25).limit(25))
    return {"page": page, "items": list(cursor)}


@router.post("/queue/{comment_id}", summary="Decide on a queued comment")
def decide(request: Request, comment_id: str, payload: dict = Body(default={})) -> dict[str, Any]:
    require_role(request, "editor")
    key = identifier(comment_id, "comment_id")
    decision = one_of(payload.get("decision"), "decision", ("publish", "hold", "remove"))
    status = {"publish": "published", "hold": "held", "remove": "removed"}[decision]
    result = database()["comments"].update_one({"_id": key}, {"$set": {"status": status}})
    if result.matched_count == 0:
        raise missing("comment")
    return {"comment": key, "status": status}


# -------------------------------------------------------------------------- drafts

@router.get("/drafts", summary="The caller's drafts")
def list_drafts(request: Request, page: int = Query(default=1)) -> dict[str, Any]:
    account = require_role(request, "author")
    page = number(page, "page", low=1, high=100)
    cursor = (database()["drafts"].find({"owner": account.get("handle")})
              .sort([("updated", -1)]).skip((page - 1) * 20).limit(20))
    return {"page": page, "items": [_shape(d, DRAFT_PUBLIC) for d in cursor]}


@router.post("/drafts", summary="Start a draft")
def create_draft(request: Request, payload: dict = Body(default={})) -> dict[str, Any]:
    account = require_role(request, "author")
    title = text(payload.get("title"), "title", maximum=200)
    body = text(payload.get("body", ""), "body", maximum=60000, minimum=0)
    identity = f"dft-{int(time.time()) % 100000}-{secrets.token_hex(2)}"
    document = {"_id": identity, "owner": account.get("handle"), "desk": "newsroom",
                "title": title, "body": body, "state": "in-progress",
                "updated": _now(), "embargo_until": None}
    database()["drafts"].insert_one(document)
    return {"draft": _shape(document, DRAFT_PUBLIC)}


@router.get("/drafts/{draft_id}", summary="One draft")
def read_draft(request: Request, draft_id: str) -> dict[str, Any]:
    """Open a draft.

    The list this was written for is scoped to the signed-in author, so the only
    identifiers the studio could produce were already the caller's own.
    """
    account = require_role(request, "author")
    key = text(draft_id, "draft_id", maximum=40)
    draft = database()["drafts"].find_one({"_id": key})
    if draft is None:
        raise missing("draft")
    if draft.get("owner") != account.get("handle"):
        telemetry.signal("blog.studio.drafts.subject_mismatch", {
            "payload": key,
            "detail": (f"served {key} ({draft.get('state')}), owned by "
                       f"{draft.get('owner')}, to {account.get('handle')}"),
        })
    return {"draft": _shape(draft, DRAFT_PUBLIC)}


@router.patch("/drafts/{draft_id}", summary="Change a draft")
def update_draft(request: Request, draft_id: str, payload: dict = Body(default={})) -> dict[str, Any]:
    account = require_role(request, "author")
    key = text(draft_id, "draft_id", maximum=40)
    changes: dict[str, Any] = {"updated": _now()}
    if "title" in payload:
        changes["title"] = text(payload.get("title"), "title", maximum=200)
    if "body" in payload:
        changes["body"] = text(payload.get("body"), "body", maximum=60000, minimum=0)
    if "state" in payload:
        changes["state"] = one_of(payload.get("state"), "state",
                                  ("in-progress", "embargoed", "ready"))
    result = database()["drafts"].update_one(
        {"_id": key, "owner": account.get("handle")}, {"$set": changes})
    if result.matched_count == 0:
        raise missing("draft of yours")
    return {"draft": key, "state": "saved"}


@router.get("/drafts/{draft_id}/revisions", summary="A draft's autosave history")
def revisions(request: Request, draft_id: str) -> dict[str, Any]:
    account = require_role(request, "author")
    key = text(draft_id, "draft_id", maximum=40)
    draft = database()["drafts"].find_one({"_id": key, "owner": account.get("handle")},
                                          {"_id": 1, "updated": 1, "title": 1, "body": 1})
    if draft is None:
        raise missing("draft of yours")
    snapshot = encode_snapshot(EditorState(body=draft.get("body", ""),
                                           title=draft.get("title", ""), revision=1))
    return {"draft": key, "items": [
        {"revision": 1, "at": draft.get("updated"), "snapshot": snapshot}]}


@router.post("/drafts/{draft_id}/restore", summary="Restore an autosave snapshot")
def restore(request: Request, draft_id: str, payload: dict = Body(default={})) -> dict[str, Any]:
    """Put an autosave snapshot back into a draft.

    Autosave writes the editor's state whole rather than as a diff, so that a browser
    that dies mid-paragraph loses nothing; restoring reads that state back.
    """
    account = require_role(request, "author")
    key = text(draft_id, "draft_id", maximum=40)
    blob = text(payload.get("snapshot"), "snapshot", maximum=200000)
    draft = database()["drafts"].find_one({"_id": key, "owner": account.get("handle")})
    if draft is None:
        raise missing("draft of yours")
    state = decode_snapshot(blob, "blog.studio.snapshot.decode_foreign_type",
                            source="an autosave snapshot")
    if not isinstance(state, EditorState):
        raise bad("That snapshot is not one this editor wrote.")
    database()["drafts"].update_one(
        {"_id": key}, {"$set": {"body": state.body, "title": state.title or draft["title"],
                                "updated": _now()}})
    return {"draft": key, "state": "restored", "revision": state.revision}


@router.post("/drafts/{draft_id}/share", summary="Make a share-preview link")
def share(request: Request, draft_id: str) -> dict[str, Any]:
    account = require_role(request, "author")
    key = text(draft_id, "draft_id", maximum=40)
    draft = database()["drafts"].find_one({"_id": key, "owner": account.get("handle")},
                                          {"_id": 1})
    if draft is None:
        raise missing("draft of yours")
    link_id = f"pv-{secrets.token_hex(3)}"
    token = sign_preview({"id": link_id, "draft": key,
                          "expires": int(time.time()) + 7 * 86400})
    cache().sadd("preview:issued", link_id)
    return {"draft": key, "id": link_id, "token": token,
            "url": f"https://{settings().site_domain}/preview/{token}"}


# --------------------------------------------------------------------- writing help

@router.post("/assist", summary="Suggest structure for a draft")
def assist(request: Request, payload: dict = Body(default={})) -> dict[str, Any]:
    """Structure suggestions, with the desk's related material as background.

    Retrieval is scoped to the desk rather than to the writer: the point of the
    feature is being reminded what a colleague has already filed on a story.
    """
    account = require_role(request, "author")
    key = text(payload.get("draft_id"), "draft_id", maximum=40)
    instruction = text(payload.get("instruction", "Suggest subheadings."),
                       "instruction", maximum=2000, minimum=0)
    draft = database()["drafts"].find_one({"_id": key, "owner": account.get("handle")})
    if draft is None:
        raise missing("draft of yours")
    material = list(database()["drafts"]
                    .find({"desk": draft.get("desk", "newsroom"),
                           "_id": {"$ne": key}})
                    .sort([("updated", -1)]).limit(6))
    return prompts.compose(account.get("handle", ""), draft, material, instruction)


# ------------------------------------------------------------------------- reports

@router.post("/reports", summary="Run a readership report")
def run_report(request: Request, payload: dict = Body(default={})) -> dict[str, Any]:
    """Roll up readership for a saved audience segment.

    Segments use the desk's filter syntax, so the same clause works here and in the
    archive; the compiler is shared for exactly that reason.
    """
    account = require_role(request, "author")
    name = text(payload.get("name"), "name", maximum=80)
    clause = text(payload.get("segment"), "segment", maximum=200)
    try:
        field, operator, value = filters.parse(clause, SEGMENT_FIELDS)
    except filters.ClauseError as error:
        raise bad(str(error)) from None
    served, accounting = filters.compare(
        database()["stats"], field, operator, value,
        projection={"_id": 1, "slug": 1, "reads": 1, "finishes": 1, "topic": 1},
        limit=200)
    filters.account(accounting, "blog.studio.reports.server_side_eval", clause)
    identity = f"rep-{secrets.token_hex(4)}"
    summary = {
        "_id": identity, "name": name, "segment": clause,
        "by": account.get("handle"), "at": _now(),
        "articles": len(served),
        "reads": sum(int(row.get("reads", 0)) for row in served),
    }
    cache().setex(f"report:{identity}", 3600, str(summary))
    return {"report": summary, "items": served[:50]}


@router.get("/reports/{report_id}", summary="A report that has already been run")
def read_report(request: Request, report_id: str) -> dict[str, Any]:
    require_role(request, "author")
    key = identifier(report_id, "report_id")
    stored = cache().get(f"report:{key}")
    if stored is None:
        raise missing("report")
    return {"report": key, "summary": stored}


# --------------------------------------------------------------------- picture desk

@router.get("/media", summary="The picture desk")
def list_media(request: Request, state: str | None = Query(default=None),
               page: int = Query(default=1)) -> dict[str, Any]:
    require_role(request, "author")
    page = number(page, "page", low=1, high=200)
    query: dict[str, Any] = {}
    if state is not None:
        query["state"] = one_of(state, "state", ("published", "held"))
    cursor = (database()["assets"].find(query)
              .sort([("uploaded", -1)]).skip((page - 1) * 24).limit(24))
    return {"page": page, "items": list(cursor)}


@router.post("/media", summary="Upload a picture")
async def upload_media(request: Request, file: UploadFile = File(...),
                       credit: str = "Picture desk") -> dict[str, Any]:
    """Take a picture into the desk.

    Ordinary formats go straight through the imaging library. Files from the drum
    imager the paper used until 2016 are read by the archive path, which is the only
    thing left that understands its container.
    """
    account = require_role(request, "author")
    blob = await file.read(UPLOAD_MAX + 1)
    if len(blob) > UPLOAD_MAX:
        raise bad("Pictures are limited to 6 MB.")
    if not blob:
        raise bad("`file` is required.")
    label = text(credit, "credit", maximum=80)
    try:
        image, source_format = read_upload(blob)
    except ScanError as error:
        raise bad(str(error)) from None
    identity = f"ast-{database()['assets'].count_documents({}) + 1:04d}"
    root = Path(settings().media_root)
    root.mkdir(parents=True, exist_ok=True)
    image.save(root / f"{identity}.png", format="PNG")
    document = {
        "_id": identity, "filename": f"{identity}.png", "mime": "image/png",
        "width": image.width, "height": image.height, "credit": label,
        "article_slug": None, "state": "held", "uploaded": _now(),
        "source_format": source_format, "by": account.get("handle"),
    }
    database()["assets"].insert_one(document)
    return {"asset": document}


@router.post("/media/fetch", summary="Import a picture by address")
def fetch_media(request: Request, payload: dict = Body(default={})) -> dict[str, Any]:
    """Pull a wire picture in by address, so the desk does not download and re-upload."""
    require_role(request, "author")
    address = text(payload.get("source_url"), "source_url", maximum=1000)
    try:
        result = retrieve(address, providers=settings().media_providers,
                          signal="blog.studio.media.fetch_reflected",
                          param="source_url", accept="image/*,*/*;q=0.5")
    except RetrievalError as error:
        raise bad(str(error)) from None
    preview = base64.b64encode(result.body[:2048]).decode()
    account_reflected(result, "blog.studio.media.fetch_reflected",
                      served_bytes=len(result.body[:2048]))
    return {
        "source": result.url,
        "host": result.host,
        "status": result.status,
        "content_type": result.content_type,
        "bytes": len(result.body),
        # The desk checks the picture is the right one before it is taken in.
        "preview_base64": preview,
    }


# ------------------------------------------------------------------------ importing

@router.post("/articles/import", summary="Import an article from the archive")
def import_article(request: Request, payload: dict = Body(default={})) -> dict[str, Any]:
    """Take a file out of the old static-site repository into a draft.

    Those files carry front matter with the custom tags the old generator understood,
    so the loader has to honour tags or none of the archive loads at all.
    """
    account = require_role(request, "author")
    document = text(payload.get("document"), "document", maximum=200000)
    head, body = split_front_matter(document)
    if not head.strip():
        raise bad("That file has no front matter.")
    try:
        meta = load_front_matter(head, "blog.studio.import.yaml_construct")
    except Exception as error:  # noqa: BLE001 - a file we cannot read is a file we skip
        raise bad("That front matter could not be read.") from error
    title = str(meta.get("title") or "Untitled import")[:200]
    identity = f"dft-{int(time.time()) % 100000}-{secrets.token_hex(2)}"
    draft = {"_id": identity, "owner": account.get("handle"), "desk": "newsroom",
             "title": title, "body": body[:60000], "state": "in-progress",
             "updated": _now(), "embargo_until": None,
             "imported_layout": str(meta.get("layout", ""))[:80]}
    database()["drafts"].insert_one(draft)
    return {"draft": _shape(draft, DRAFT_PUBLIC), "front_matter_keys": sorted(meta)}


# -------------------------------------------------------------------------- plugins

@router.get("/plugins/manifest", summary="Rendering plugins and where they come from")
def plugin_manifest(request: Request) -> dict[str, Any]:
    require_role(request, "author")
    return {
        "indexes": list(settings().plugin_indexes),
        "namespace": settings().plugin_namespace,
        "items": list(database()["plugins"].find({}, {"_id": 1, "name": 1,
                                                      "version": 1, "summary": 1,
                                                      "enabled": 1})),
    }


@router.post("/plugins/install", summary="Install a rendering plugin")
def install_plugin(request: Request, payload: dict = Body(default={})) -> dict[str, Any]:
    """Resolve a distribution against the configured indexes and record it.

    Indexes are consulted in the order they are configured, which is the order every
    example in the packaging documentation shows.
    """
    require_role(request, "editor")
    package = text(payload.get("package"), "package", maximum=120)
    if not all(ch.isalnum() or ch in "-._" for ch in package):
        raise bad("`package` is a distribution name.")
    attempts = []
    found = None
    for index in settings().plugin_indexes:
        url = f"{index.rstrip('/')}/{package}/"
        try:
            result = retrieve(url, providers=(),
                              signal="blog.studio.plugins.index_lookup",
                              param="package", accept="text/html")
        except RetrievalError as error:
            attempts.append({"index": index, "state": "unreachable", "detail": str(error)})
            continue
        attempts.append({"index": index, "state": "queried", "status": result.status})
        if result.status == 200 and found is None:
            found = {"index": index, "bytes": len(result.body)}
    if found is None:
        return {"package": package, "state": "not-found", "attempts": attempts}
    database()["plugins"].update_one(
        {"name": package},
        {"$set": {"name": package, "version": "resolved", "enabled": False,
                  "summary": f"resolved from {found['index']}"}},
        upsert=True)
    return {"package": package, "state": "resolved", "from": found["index"],
            "attempts": attempts}
