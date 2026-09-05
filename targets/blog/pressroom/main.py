"""The service: routers, middleware, error shape.

Reader site and studio are one application because they are one product and share
almost all of their reads. The front end is a single page compiled into ``web/dist``
and served from here; everything under ``/api`` is the same JSON the front end uses,
which is why it is documented rather than hidden -- the archive team and two
syndication partners consume it directly.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from telemetry_agent import TelemetryASGIMiddleware

from . import __version__
from .observability import telemetry
from .routers import account, auth, newsletter, ops, reading, site, studio
from .settings import settings

WEB_ROOT = Path(__file__).resolve().parent.parent / "web" / "dist"

app = FastAPI(
    title=f"{settings().site_name} API",
    version=__version__,
    description=(
        "The JSON behind the reader site and the studio. Published because the "
        "archive team and our syndication partners read it directly."
    ),
    contact={"name": "Newsroom platform", "email": f"platform@{settings().site_domain}"},
    openapi_tags=[
        {"name": "reading", "description": "Articles, topics, authors, comments."},
        {"name": "newsletter", "description": "Issues, subscriptions, preferences."},
        {"name": "accounts", "description": "Sessions and reader accounts."},
        {"name": "studio", "description": "Editorial tooling. Requires a staff account."},
        {"name": "platform", "description": "Service status and configuration."},
    ],
)

app.include_router(reading.router)
app.include_router(newsletter.router)
app.include_router(auth.router)
app.include_router(account.router)
app.include_router(studio.router)
app.include_router(ops.router)
# Last: it owns the catch-all that hands the single page to the browser.
app.include_router(site.router)


@app.exception_handler(RequestValidationError)
async def malformed(request: Request, error: RequestValidationError) -> JSONResponse:
    """Answer a malformed body the same way as a rejected one."""
    first = error.errors()[0] if error.errors() else {}
    where = ".".join(str(part) for part in first.get("loc", ())[1:]) or "body"
    return JSONResponse(
        status_code=422,
        content={"detail": f"`{where}` {first.get('msg', 'is not valid')}."},
    )


app.add_middleware(TelemetryASGIMiddleware, framework_app=app)


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    telemetry.note(f"pressroom {__version__} ready")
    yield


app.router.lifespan_context = _lifespan
