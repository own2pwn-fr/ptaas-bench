"""What a browser asks for that is not the API: the page itself and the site's metadata.

The front end is one compiled page; every reader route is resolved in the browser, so
anything that is not a file we ship and not an API call is answered with it.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, Response

from ..settings import settings
from ..store import database

router = APIRouter(include_in_schema=False)

WEB_ROOT = Path(__file__).resolve().parents[2] / "web" / "dist"
ASSET_TYPES = {
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/vnd.microsoft.icon",
    ".woff2": "font/woff2",
    ".json": "application/json",
    ".webmanifest": "application/manifest+json",
    ".txt": "text/plain; charset=utf-8",
    ".xml": "application/xml; charset=utf-8",
}
PAGE_CACHE = "public, max-age=60"
ASSET_CACHE = "public, max-age=31536000, immutable"


_SHELL: str | None = None


def _shell() -> str:
    """The compiled shell with this deployment's constants substituted in.

    The bundle is built once and deployed everywhere, so the values that differ per
    deployment -- the publication's own name, the counting site, the key the studio
    signs share links with -- are put into the shell here rather than compiled in.
    """
    global _SHELL
    if _SHELL is not None:
        return _SHELL
    index = WEB_ROOT / "index.html"
    if not index.exists():
        raise HTTPException(status_code=503, detail="The site is being deployed.")
    cfg = settings()
    bootstrap = {
        "apiBase": "/api",
        "publication": cfg.site_name,
        "domain": cfg.site_domain,
        "analytics": {"site": cfg.analytics_site_id},
        "preview": {"key": cfg.preview_signing_key, "days": 7},
        "limits": {"comment": 4000, "search": 1500},
    }
    _SHELL = (index.read_text(encoding="utf-8")
              .replace("__PRESSROOM_BOOTSTRAP__", json.dumps(bootstrap))
              .replace("__PRESSROOM_TITLE__", cfg.site_name)
              .replace("__PRESSROOM_ANALYTICS__", cfg.analytics_site_id))
    return _SHELL


def _page() -> HTMLResponse:
    return HTMLResponse(_shell(), headers={"cache-control": PAGE_CACHE})


@router.get("/assets/{name}")
def asset(name: str) -> Response:
    if "/" in name or ".." in name:
        raise HTTPException(status_code=404, detail="Not found.")
    path = WEB_ROOT / "assets" / name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Not found.")
    return FileResponse(path, media_type=ASSET_TYPES.get(path.suffix, "application/octet-stream"),
                        headers={"cache-control": ASSET_CACHE})


@router.get("/favicon.ico")
def favicon() -> Response:
    path = WEB_ROOT / "favicon.ico"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Not found.")
    return FileResponse(path, media_type="image/vnd.microsoft.icon",
                        headers={"cache-control": ASSET_CACHE})


@router.get("/site.webmanifest")
def webmanifest() -> Response:
    cfg = settings()
    return JSONResponse({
        "name": cfg.site_name,
        "short_name": cfg.site_name.split()[0],
        "start_url": "/",
        "display": "standalone",
        "background_color": "#14181d",
        "theme_color": "#14181d",
        "icons": [{"src": "/assets/icon-192.png", "sizes": "192x192", "type": "image/png"}],
    })


@router.get("/robots.txt")
def robots() -> PlainTextResponse:
    cfg = settings()
    return PlainTextResponse(
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /studio\n"
        "Disallow: /account\n"
        "Disallow: /preview/\n"
        "Disallow: /api/\n"
        "Crawl-delay: 2\n"
        "\n"
        "User-agent: GPTBot\n"
        "Disallow: /\n"
        "\n"
        f"Sitemap: https://{cfg.site_domain}/sitemap.xml\n",
        headers={"cache-control": "public, max-age=3600"})


@router.get("/humans.txt")
def humans() -> PlainTextResponse:
    cfg = settings()
    return PlainTextResponse(
        f"/* {cfg.site_name} */\n\n"
        "Newsroom: the desk, the picture desk, and one very patient sub.\n"
        "Platform: built in-house. Python, Vue, MongoDB.\n"
        f"Contact: platform@{cfg.site_domain}\n",
        headers={"cache-control": "public, max-age=86400"})


@router.get("/.well-known/security.txt")
def security_txt() -> PlainTextResponse:
    cfg = settings()
    year = datetime.now(timezone.utc).year + 1
    return PlainTextResponse(
        f"Contact: mailto:security@{cfg.site_domain}\n"
        f"Expires: {year}-01-01T00:00:00.000Z\n"
        "Preferred-Languages: en\n"
        f"Canonical: https://{cfg.site_domain}/.well-known/security.txt\n",
        headers={"cache-control": "public, max-age=86400"})


@router.get("/opensearch.xml")
def opensearch() -> Response:
    cfg = settings()
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<OpenSearchDescription xmlns="http://a9.com/-/spec/opensearch/1.1/">\n'
        f"  <ShortName>{cfg.site_name}</ShortName>\n"
        f"  <Description>Search {cfg.site_name}</Description>\n"
        f'  <Url type="text/html" template="https://{cfg.site_domain}/search?q={{searchTerms}}"/>\n'
        "</OpenSearchDescription>\n"
    )
    return Response(body, media_type="application/opensearchdescription+xml")


@router.get("/sitemap.xml")
def sitemap() -> Response:
    cfg = settings()
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"  <sitemap><loc>https://{cfg.site_domain}/sitemap-pages.xml</loc></sitemap>\n"
        f"  <sitemap><loc>https://{cfg.site_domain}/sitemap-articles.xml</loc></sitemap>\n"
        "</sitemapindex>\n"
    )
    return Response(body, media_type="application/xml",
                    headers={"cache-control": "public, max-age=3600"})


@router.get("/sitemap-pages.xml")
def sitemap_pages() -> Response:
    cfg = settings()
    paths = ["/", "/articles", "/topics", "/authors", "/archive", "/search",
             "/newsletter", "/about", "/contact", "/ethics", "/privacy", "/terms",
             "/corrections", "/signin", "/register"]
    entries = "".join(
        f"  <url><loc>https://{cfg.site_domain}{path}</loc></url>\n" for path in paths)
    return Response(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + entries + "</urlset>\n",
        media_type="application/xml", headers={"cache-control": "public, max-age=3600"})


@router.get("/sitemap-articles.xml")
def sitemap_articles() -> Response:
    cfg = settings()
    cursor = (database()["articles"].find({"status": "published"},
                                          {"slug": 1, "published_at": 1})
              .sort([("published_at", -1)]).limit(2000))
    entries = "".join(
        f"  <url><loc>https://{cfg.site_domain}/articles/{doc['slug']}</loc>"
        f"<lastmod>{str(doc.get('published_at'))[:10]}</lastmod></url>\n"
        for doc in cursor)
    return Response(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + entries + "</urlset>\n",
        media_type="application/xml", headers={"cache-control": "public, max-age=3600"})


@router.get("/feed.xml")
def feed() -> Response:
    cfg = settings()
    cursor = (database()["articles"].find({"status": "published"},
                                          {"slug": 1, "title": 1, "standfirst": 1,
                                           "published_at": 1})
              .sort([("published_at", -1)]).limit(30))
    items = "".join(
        "    <item>\n"
        f"      <title>{_xml(doc.get('title', ''))}</title>\n"
        f"      <link>https://{cfg.site_domain}/articles/{doc['slug']}</link>\n"
        f"      <guid>https://{cfg.site_domain}/articles/{doc['slug']}</guid>\n"
        f"      <description>{_xml(doc.get('standfirst', ''))}</description>\n"
        "    </item>\n"
        for doc in cursor)
    return Response(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0"><channel>\n'
        f"  <title>{_xml(cfg.site_name)}</title>\n"
        f"  <link>https://{cfg.site_domain}/</link>\n"
        f"  <description>Reporting from the harbour town.</description>\n"
        + items + "</channel></rss>\n",
        media_type="application/rss+xml", headers={"cache-control": "public, max-age=900"})


@router.get("/feed.json")
def feed_json() -> Response:
    cfg = settings()
    cursor = (database()["articles"].find({"status": "published"},
                                          {"slug": 1, "title": 1, "standfirst": 1,
                                           "published_at": 1})
              .sort([("published_at", -1)]).limit(30))
    return JSONResponse({
        "version": "https://jsonfeed.org/version/1.1",
        "title": cfg.site_name,
        "home_page_url": f"https://{cfg.site_domain}/",
        "feed_url": f"https://{cfg.site_domain}/feed.json",
        "items": [{
            "id": f"https://{cfg.site_domain}/articles/{doc['slug']}",
            "url": f"https://{cfg.site_domain}/articles/{doc['slug']}",
            "title": doc.get("title", ""),
            "summary": doc.get("standfirst", ""),
            "date_published": str(doc.get("published_at")),
        } for doc in cursor],
    }, headers={"cache-control": "public, max-age=900"})


def _xml(value: str) -> str:
    return (value.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


@router.get("/{full_path:path}")
def page(request: Request, full_path: str) -> Response:
    """Every reader route is resolved in the browser, so they all get the page."""
    if full_path.startswith("api/"):
        raise HTTPException(status_code=404, detail="No such endpoint.")
    return _page()


@router.get("/")
def home() -> Response:
    return _page()
