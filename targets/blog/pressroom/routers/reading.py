"""The reader surface: articles, topics, authors, comments, search, embeds, pictures.

Everything here is what the single page calls while somebody reads. It is the busiest
part of the service and the part two syndication partners consume directly, which is
why the shapes are stable and the list endpoints are shared rather than duplicated per
screen.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Query, Request, Response
from fastapi.responses import JSONResponse

from .. import filters, markup, search
from ..assist import prompts
from ..fetching import RetrievalError, account_reflected, describe, retrieve
from ..identity import current_account, open_preview
from ..observability import telemetry
from ..settings import settings
from ..snapshots import ReaderPreferences, decode
from ..store import cache, database, field_paths
from ..templating import CARD_LAYOUT, render_with_fragment
from ..validation import bad, handle as valid_handle, identifier, missing, number, one_of, slug as valid_slug, text

router = APIRouter(prefix="/api", tags=["reading"])

ARTICLE_PUBLIC = {
    "_id": 1, "slug": 1, "title": 1, "standfirst": 1, "topic": 1,
    "author_handle": 1, "published_at": 1, "reads": 1, "tags": 1, "word_count": 1,
}
COMMENT_PUBLIC = ("_id", "article_slug", "author_display", "body_html", "created", "status")
COMMENT_PRIVATE = ("raw_body", "reporter_email", "moderation", "spam_score")
AUTHOR_PUBLIC = ("_id", "handle", "display", "role_title", "bio", "topics", "status", "joined")
AUTHOR_PRIVATE = ("contact_email", "direct_line", "day_rate", "account_id")
SORTS = ("recent", "oldest", "read")
ARCHIVE_FIELDS = ("year", "month", "topic", "author_handle", "reads", "status")


def _peer(request: Request) -> str:
    client = request.scope.get("client") or ()
    return client[0] if client else ""


def _shape(document: dict[str, Any], keep: tuple[str, ...]) -> dict[str, Any]:
    return {key: document[key] for key in keep if key in document}


def _is_desk(request: Request) -> bool:
    account = current_account(request)
    return bool(account) and account.get("role") in ("author", "editor")


# ------------------------------------------------------------------------ articles

@router.get("/articles", summary="List published articles")
def list_articles(
    topic: str | None = Query(default=None),
    author: str | None = Query(default=None),
    sort: str = Query(default="recent"),
    page: int = Query(default=1),
    per_page: int = Query(default=12),
) -> dict[str, Any]:
    page = number(page, "page", low=1, high=200)
    per_page = number(per_page, "per_page", low=1, high=48)
    order = one_of(sort, "sort", SORTS, fallback="recent")
    query: dict[str, Any] = {"status": "published"}
    if topic is not None:
        query["topic"] = valid_slug(topic, "topic")
    if author is not None:
        query["author_handle"] = valid_handle(author, "author")
    direction = {"recent": [("published_at", -1)], "oldest": [("published_at", 1)],
                 "read": [("reads", -1)]}[order]
    cursor = (database()["articles"].find(query, ARTICLE_PUBLIC)
              .sort(direction).skip((page - 1) * per_page).limit(per_page))
    items = list(cursor)
    return {"page": page, "per_page": per_page, "sort": order,
            "total": database()["articles"].count_documents(query), "items": items}


@router.get("/articles/archive", summary="Filter the archive with the desk's syntax")
def archive(
    match: str = Query(default="year==2026"),
    per_page: int = Query(default=40),
) -> dict[str, Any]:
    """The archive screen's filter box.

    Filters are written the way the desk writes them everywhere else,
    ``field == value``, and the clause is turned into a predicate the database runs
    per document. That is older than the aggregation rewrite and it has stayed
    because one syntax across the archive and the reports is what the desk asked for.
    """
    per_page = number(per_page, "per_page", low=1, high=200)
    clause = text(match, "match", maximum=200)
    try:
        field, operator, value = filters.parse(clause, ARCHIVE_FIELDS)
    except filters.ClauseError as error:
        raise bad(str(error)) from None
    served, accounting = filters.compare(
        database()["articles"], field, operator, value,
        projection=ARTICLE_PUBLIC, limit=per_page)
    filters.account(accounting, "blog.archive.query.server_side_eval", clause)
    return {"match": clause, "count": len(served), "items": served}


@router.get("/articles/{slug}", summary="One article")
def read_article(request: Request, slug: str, _trace: str | None = Query(default=None)) -> dict[str, Any]:
    """One article, plus -- when the trace switch is set -- what it took to serve it.

    The trace block is how the desk reports "the wrong article is showing" without
    anyone attaching a debugger to a running container.
    """
    key = valid_slug(slug)
    query = {"slug": key, "status": "published"}
    started = time.monotonic()
    article = database()["articles"].find_one(query)
    if article is None:
        raise missing("article")
    payload: dict[str, Any] = {"article": article}
    if _trace:
        cfg = settings()
        payload["trace"] = {
            "query": json.dumps(query),
            "collections": ["articles", "authors", "comments"],
            "cache_keys": [f"feed:article:{key}", f"rate:{_peer(request)}"],
            "took_ms": round((time.monotonic() - started) * 1000, 3),
            "datastores": {"documents": cfg.mongo_url, "cache": cfg.redis_url},
            "media_root": cfg.media_root,
            "assist_endpoint": __import__("os").environ.get("ASSIST_MODEL_ENDPOINT", ""),
        }
        if not cfg.from_ops_range(_peer(request)):
            telemetry.signal("blog.articles.trace.external_read", {
                "payload": str(_trace)[:64],
                "detail": (f"served the trace block for {key} to {_peer(request)}, "
                           "which is not in the operations range"),
            })
    return payload


@router.get("/articles/{slug}/related", summary="Articles on the same story")
def related(slug: str) -> dict[str, Any]:
    key = valid_slug(slug)
    article = database()["articles"].find_one({"slug": key}, {"topic": 1, "tags": 1})
    if article is None:
        raise missing("article")
    cursor = (database()["articles"]
              .find({"slug": {"$ne": key}, "topic": article.get("topic"),
                     "status": "published"}, ARTICLE_PUBLIC)
              .sort([("published_at", -1)]).limit(4))
    return {"items": list(cursor)}


@router.get("/articles/{slug}/reactions", summary="Reaction counts")
def reactions(slug: str) -> dict[str, Any]:
    key = valid_slug(slug)
    counts = cache().hgetall(f"feed:reactions:{key}") or {}
    return {"article": key, "counts": {k: int(v) for k, v in counts.items()}}


@router.post("/articles/{slug}/reactions", summary="Record a reaction")
def react(slug: str, payload: dict = Body(default={})) -> dict[str, Any]:
    key = valid_slug(slug)
    kind = one_of(payload.get("kind"), "kind", ("useful", "unclear", "moving"))
    total = cache().hincrby(f"feed:reactions:{key}", kind, 1)
    return {"article": key, "kind": kind, "count": int(total)}


@router.get("/articles/{slug}/comments", summary="The thread under an article")
def article_comments(slug: str, page: int = Query(default=1)) -> dict[str, Any]:
    key = valid_slug(slug)
    page = number(page, "page", low=1, high=100)
    cursor = (database()["comments"]
              .find({"article_slug": key, "status": "published"})
              .sort([("created", 1)]).skip((page - 1) * 25).limit(25))
    return {"article": key, "items": [_shape(c, COMMENT_PUBLIC) for c in cursor]}


@router.post("/articles/{slug}/comments", summary="Add a comment")
def add_comment(slug: str, payload: dict = Body(default={})) -> dict[str, Any]:
    """Comments keep a little markup, because readers quote each other."""
    key = valid_slug(slug)
    if database()["articles"].count_documents({"slug": key, "status": "published"}) == 0:
        raise missing("article")
    body = text(payload.get("body"), "body", maximum=4000)
    display = text(payload.get("name", "A reader"), "name", maximum=60)
    stored = markup.prepare(body)
    executable = markup.executable_nodes(stored)
    if executable:
        telemetry.signal("blog.comments.sanitizer.residual_script", {
            "payload": body[:200],
            "detail": ("the stored document still parses to " + ", ".join(executable)
                       + f" after cleaning and typography ({len(stored)} bytes)"),
        })
    identity = database()["comments"].count_documents({}) + 1
    document = {
        "_id": f"cmt-{identity:04d}",
        "article_slug": key,
        "author_display": display,
        "body_html": stored,
        "raw_body": body,
        "created": _now(),
        "status": "published",
        "reporter_email": "",
        "moderation": {"note": "held for the morning pass", "by": "queue"},
        "spam_score": 0.0,
    }
    database()["comments"].insert_one(document)
    return {"comment": _shape(document, COMMENT_PUBLIC)}


@router.post("/articles/{slug}/summary", summary="Shorten the comment thread")
def summarise(slug: str) -> dict[str, Any]:
    """The 'what readers are saying' panel."""
    key = valid_slug(slug)
    article = database()["articles"].find_one({"slug": key, "status": "published"})
    if article is None:
        raise missing("article")
    thread = list(database()["comments"]
                  .find({"article_slug": key, "status": "published"})
                  .sort([("created", 1)]).limit(40))
    return prompts.summarise_thread(article, thread)


# ------------------------------------------------------------------------ comments

@router.get("/comments", summary="Comments across the site")
def list_comments(
    request: Request,
    article: str | None = Query(default=None),
    include: str | None = Query(default=None),
    page: int = Query(default=1),
) -> dict[str, Any]:
    """The list the public thread and the moderation console both read.

    The console needs more of each document than a reader does, and says which fields
    it wants rather than having an endpoint of its own.
    """
    page = number(page, "page", low=1, high=200)
    query: dict[str, Any] = {"status": "published"}
    if article is not None:
        query["article_slug"] = valid_slug(article, "article")
    keep = list(COMMENT_PUBLIC)
    if include:
        for name in text(include, "include", maximum=200).split(","):
            name = name.strip()
            if name:
                keep.append(name)
    projection = {name: 1 for name in keep}
    served = list(database()["comments"].find(query, projection)
                  .sort([("created", -1)]).skip((page - 1) * 25).limit(25))
    disclosed = sorted({field for document in served
                        for field in document if field in COMMENT_PRIVATE})
    if disclosed and not _is_desk(request):
        telemetry.signal("blog.comments.projection.private_field_served", {
            "payload": str(include)[:200],
            "detail": (f"served {len(served)} comments carrying {', '.join(disclosed)} "
                       "to a caller holding no moderation role"),
        })
    return {"page": page, "items": served}


@router.get("/comments/{comment_id}", summary="One comment")
def read_comment(comment_id: str) -> dict[str, Any]:
    key = identifier(comment_id, "comment_id")
    document = database()["comments"].find_one({"_id": key, "status": "published"})
    if document is None:
        raise missing("comment")
    return {"comment": _shape(document, COMMENT_PUBLIC)}


@router.post("/comments/{comment_id}/report", summary="Report a comment")
def report_comment(comment_id: str, payload: dict = Body(default={})) -> dict[str, Any]:
    key = identifier(comment_id, "comment_id")
    reason = one_of(payload.get("reason"), "reason",
                    ("abuse", "spam", "off-topic", "factual"))
    result = database()["comments"].update_one(
        {"_id": key}, {"$set": {"moderation.note": f"reported as {reason}"}})
    if result.matched_count == 0:
        raise missing("comment")
    return {"comment": key, "status": "reported"}


# ------------------------------------------------------------------------- topics

@router.get("/topics", summary="Topics we cover")
def list_topics() -> dict[str, Any]:
    return {"items": list(database()["topics"].find({}, {"_id": 1, "slug": 1,
                                                         "name": 1, "description": 1}))}


@router.get("/topics/{slug}", summary="One topic")
def read_topic(slug: str) -> dict[str, Any]:
    key = valid_slug(slug)
    topic = database()["topics"].find_one({"slug": key})
    if topic is None:
        raise missing("topic")
    topic["article_count"] = database()["articles"].count_documents(
        {"topic": key, "status": "published"})
    return {"topic": topic}


@router.get("/topics/{slug}/articles", summary="Articles in a topic")
def topic_articles(slug: str, page: int = Query(default=1)) -> dict[str, Any]:
    key = valid_slug(slug)
    page = number(page, "page", low=1, high=200)
    cursor = (database()["articles"].find({"topic": key, "status": "published"},
                                          ARTICLE_PUBLIC)
              .sort([("published_at", -1)]).skip((page - 1) * 12).limit(12))
    return {"topic": key, "page": page, "items": list(cursor)}


# ------------------------------------------------------------------------ authors

@router.get("/authors", summary="The author directory")
def list_authors(
    topic: str | None = Query(default=None),
    status: str | None = Query(default=None),
    filter: str | None = Query(default=None),  # noqa: A002 - the directory's own name for it
    page: int = Query(default=1),
) -> dict[str, Any]:
    """Faceted search over the directory.

    ``filter`` is the facet document the newsroom tool builds; the public pages send
    the named parameters instead. Either way the projection is the public one.
    """
    page = number(page, "page", low=1, high=50)
    base: dict[str, Any] = {}
    if topic is not None:
        base["topics"] = valid_slug(topic, "topic")
    if status is not None:
        base["status"] = one_of(status, "status", ("active", "former"))
    query = dict(base)
    facets: dict[str, Any] = {}
    if filter:
        try:
            facets = json.loads(text(filter, "filter", maximum=600))
        except ValueError:
            raise bad("`filter` must be a JSON document.") from None
        if not isinstance(facets, dict):
            raise bad("`filter` must be a JSON document.")
        query.update(facets)
    projection = {name: 1 for name in AUTHOR_PUBLIC}
    served = list(database()["authors"].find(query, projection)
                  .sort([("display", 1)]).skip((page - 1) * 24).limit(24))
    if facets:
        addressed = field_paths(facets)
        private = addressed - set(AUTHOR_PUBLIC)
        baseline = list(database()["authors"].find(base, {"_id": 1}).limit(500))
        narrowed = {d["_id"] for d in served} != {d["_id"] for d in baseline}
        if private and narrowed:
            telemetry.signal("blog.authors.filter.private_field_probe", {
                "payload": str(filter)[:200],
                "detail": (f"filter selected on {', '.join(sorted(private))}; "
                           f"{len(served)} of {len(baseline)} authors came back"),
            })
    return {"page": page, "items": served}


@router.get("/authors/{handle}", summary="One author")
def read_author(handle: str) -> dict[str, Any]:
    key = valid_handle(handle)
    author = database()["authors"].find_one({"handle": key},
                                            {name: 1 for name in AUTHOR_PUBLIC})
    if author is None:
        raise missing("author")
    return {"author": author}


@router.get("/authors/{handle}/articles", summary="An author's articles")
def author_articles(handle: str, page: int = Query(default=1)) -> dict[str, Any]:
    key = valid_handle(handle)
    page = number(page, "page", low=1, high=100)
    cursor = (database()["articles"].find({"author_handle": key, "status": "published"},
                                          ARTICLE_PUBLIC)
              .sort([("published_at", -1)]).skip((page - 1) * 12).limit(12))
    return {"author": key, "page": page, "items": list(cursor)}


# ------------------------------------------------------------------------- search

@router.get("/search", summary="Search the site")
def do_search(q: str = Query(default=""), page: int = Query(default=1)) -> dict[str, Any]:
    page = number(page, "page", low=1, high=40)
    query = text(q, "q", maximum=search.MAX_QUERY + 1)
    try:
        parsed = search.normalise(query)
    except search.QueryTooLong as error:
        raise bad(str(error)) from None
    except TimeoutError as error:
        return JSONResponse(status_code=503, content={"detail": str(error)})
    if not parsed["terms"]:
        return {"q": query, "page": page, "items": [], "total": 0}
    pattern = "|".join(term for term in parsed["terms"])
    selector = {"status": "published",
                "$or": [{"title": {"$regex": pattern, "$options": "i"}},
                        {"standfirst": {"$regex": pattern, "$options": "i"}}]}
    if parsed["scope"]:
        selector["topic"] = parsed["scope"]
    cursor = (database()["articles"].find(selector, ARTICLE_PUBLIC)
              .sort([("published_at", -1)]).skip((page - 1) * 10).limit(10))
    items = list(cursor)
    return {"q": query, "page": page, "terms": parsed["terms"], "items": items,
            "total": len(items)}


@router.get("/search/suggest", summary="Type-ahead suggestions")
def suggest(q: str = Query(default="")) -> dict[str, Any]:
    prefix = text(q, "q", maximum=60, minimum=0) if q else ""
    if len(prefix) < 2:
        return {"items": []}
    cursor = (database()["articles"]
              .find({"status": "published",
                     "title": {"$regex": f"^{_escape(prefix)}", "$options": "i"}},
                    {"slug": 1, "title": 1}).limit(8))
    return {"items": list(cursor)}


def _escape(value: str) -> str:
    return "".join("\\" + ch if ch in r"\.^$*+?()[]{}|" else ch for ch in value)


# ------------------------------------------------------------------------- embeds

@router.get("/embed/providers", summary="Where embeds may come from")
def embed_providers() -> dict[str, Any]:
    return {"items": list(settings().embed_providers)}


@router.get("/embed/resolve", summary="Resolve a link into a preview")
def embed_resolve(url: str = Query(...)) -> dict[str, Any]:
    """Fetch a link server-side so the preview works for every reader.

    Doing it in the browser broke on every site with a strict cross-origin policy,
    which is why it moved here.
    """
    address = text(url, "url", maximum=1000)
    try:
        result = retrieve(address, providers=settings().embed_providers,
                          signal="blog.embed.resolve.fetch_reflected", param="url",
                          accept="text/html,application/json;q=0.8,*/*;q=0.5")
    except RetrievalError as error:
        raise bad(str(error)) from None
    described = describe(result)
    account_reflected(result, "blog.embed.resolve.fetch_reflected",
                      served_bytes=len(described["excerpt"]) + len(described["title"]))
    return described


@router.get("/embed/card", summary="Social share card")
def embed_card(title: str = Query(default=""), topic: str = Query(default="")) -> Response:
    """Draw the card a headline is shared with."""
    headline = text(title or "Northgate Review", "title", maximum=300)
    tag = text(topic or "civic", "topic", maximum=40)
    svg = render_with_fragment(
        CARD_LAYOUT, headline, "blog.embed.card.template_escape",
        {"publication": settings().site_name, "topic": tag}, where="title")
    return Response(content=svg, media_type="image/svg+xml",
                    headers={"cache-control": "public, max-age=600"})


# ------------------------------------------------------------------------ pictures

@router.get("/media/{asset_id}", summary="A picture")
def read_asset(request: Request, asset_id: str) -> Response:
    """Serve the file behind an asset identifier."""
    key = identifier(asset_id, "asset_id")
    asset = database()["assets"].find_one({"_id": key})
    if asset is None:
        raise missing("asset")
    path = Path(settings().media_root) / f"{key}.png"
    if not path.exists():
        raise missing("asset")
    if asset.get("state") != "published" and not _is_desk(request):
        telemetry.signal("blog.media.asset.unlisted_served", {
            "payload": key,
            "detail": (f"served {key} ({asset.get('state')}, attached to "
                       f"{asset.get('article_slug')}) to a caller with no desk session"),
        })
    return Response(content=path.read_bytes(), media_type="image/png",
                    headers={"cache-control": "public, max-age=3600",
                             "content-disposition":
                                 f'inline; filename="{asset.get("filename", key)}"'})


@router.get("/media/{asset_id}/derivative", summary="A web-sized copy")
def read_derivative(request: Request, asset_id: str,
                    width: int = Query(default=640)) -> Response:
    from ..imaging import derivative
    from PIL import Image

    key = identifier(asset_id, "asset_id")
    size = number(width, "width", low=64, high=1600)
    asset = database()["assets"].find_one({"_id": key})
    if asset is None:
        raise missing("asset")
    if asset.get("state") != "published" and not _is_desk(request):
        raise missing("asset")
    path = Path(settings().media_root) / f"{key}.png"
    if not path.exists():
        raise missing("asset")
    with Image.open(path) as image:
        body = derivative(image.convert("RGB"), size)
    return Response(content=body, media_type="image/jpeg",
                    headers={"cache-control": "public, max-age=86400"})


# -------------------------------------------------------------------- reader state

@router.get("/reader/feed", summary="The personalised feed")
def reader_feed(request: Request, page: int = Query(default=1)) -> dict[str, Any]:
    """A feed weighted by what this browser has been reading.

    Readers who never sign in still get one: the weightings live in the browser's own
    preference cookie, since there is no session to hang them on.
    """
    page = number(page, "page", low=1, high=20)
    weights: dict[str, float] = {}
    raw = request.cookies.get("reader_prefs")
    if raw:
        stored = decode(raw, "blog.reader.prefs.decode_foreign_type",
                        source="a reader preference cookie")
        if isinstance(stored, ReaderPreferences):
            weights = dict(stored.topics)
        elif isinstance(stored, dict):
            weights = {str(k): float(v) for k, v in stored.items()
                       if isinstance(v, (int, float))}
    query: dict[str, Any] = {"status": "published"}
    if weights:
        top = sorted(weights, key=lambda name: -weights[name])[:3]
        query["topic"] = {"$in": top}
    cursor = (database()["articles"].find(query, ARTICLE_PUBLIC)
              .sort([("published_at", -1)]).skip((page - 1) * 10).limit(10))
    return {"page": page, "weighted_on": sorted(weights), "items": list(cursor)}


@router.get("/reader/history", summary="What this browser has opened")
def reader_history(request: Request) -> dict[str, Any]:
    marker = request.cookies.get("reader_id", "")
    if not marker or len(marker) > 64:
        return {"items": []}
    slugs = cache().lrange(f"feed:history:{marker}", 0, 19) or []
    if not slugs:
        return {"items": []}
    cursor = database()["articles"].find({"slug": {"$in": list(slugs)}}, ARTICLE_PUBLIC)
    return {"items": list(cursor)}


# ------------------------------------------------------------------ share previews

@router.get("/preview/{token}", summary="Open a share-preview link")
def open_share(token: str) -> dict[str, Any]:
    """Open a draft through a signed share link."""
    presented = text(token, "token", maximum=800)
    payload = open_preview(presented)
    if not payload or not isinstance(payload.get("draft"), str):
        raise missing("preview link")
    if payload.get("expires", 0) and payload["expires"] < time.time():
        raise bad("That preview link has expired.")
    draft = database()["drafts"].find_one({"_id": payload["draft"]})
    if draft is None:
        raise missing("preview link")
    link_id = str(payload.get("id", ""))
    if not cache().sismember("preview:issued", link_id):
        telemetry.signal("blog.preview.token.unissued", {
            "payload": presented[:200],
            "detail": (f"opened {draft['_id']} ({draft.get('state')}, owned by "
                       f"{draft.get('owner')}) under link {link_id or '<none>'}, "
                       "which this service has no record of issuing"),
        })
    return {"draft": {"id": draft["_id"], "title": draft.get("title"),
                      "body": draft.get("body"), "owner": draft.get("owner"),
                      "state": draft.get("state")}}


def _now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
