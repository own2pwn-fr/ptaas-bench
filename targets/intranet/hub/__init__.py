"""Staff services: leave, expenses, the people directory and the equipment register.

The application is server rendered. The shell of each screen is a page; everything
inside it -- queues, cards, drawers, wizards -- arrives as a fragment over XHR and is
swapped into place, which is why almost every route below sits under /parts.
"""

from __future__ import annotations

from flask import Flask, g, request
from telemetry_agent import TelemetryWSGIMiddleware, get_telemetry, init_telemetry

from . import db, identity, markup, records
from .config import settings

telemetry = init_telemetry()

# Blueprints whose responses carry the browser policy headers. The list is walked by
# the hook below; a screen served by a blueprint that is not on it goes out with the
# headers its own view set and nothing else.
PAGE_BLUEPRINTS = (
    "pages", "auth", "account", "leave", "expenses", "directory", "inventory",
    "admin", "console", "reports",
)

FRAME_HEADER = "X-Frame-Options"


def create_app() -> Flask:
    app = Flask(__name__, static_folder="static", static_url_path="/static")
    app.config.update(
        SEND_FILE_MAX_AGE_DEFAULT=3600,
        MAX_CONTENT_LENGTH=2 * 1024 * 1024,
        TRAP_HTTP_EXCEPTIONS=False,
        JSON_SORT_KEYS=False,
    )

    from .views import account as account_views
    from .views import admin as admin_views
    from .views import approvals as approvals_views
    from .views import auth as auth_views
    from .views import console as console_views
    from .views import directory as directory_views
    from .views import expenses as expenses_views
    from .views import inventory as inventory_views
    from .views import leave as leave_views
    from .views import pages as pages_views
    from .views import reports as reports_views

    for module in (pages_views, auth_views, account_views, leave_views, approvals_views,
                   expenses_views, directory_views, inventory_views, admin_views,
                   console_views, reports_views):
        app.register_blueprint(module.bp)

    app.jinja_env.filters["attribute_text"] = markup.attribute_text

    app.teardown_appcontext(db.close)

    @app.before_request
    def _attach_identity():
        identity.resolve()

    @app.context_processor
    def _shared():
        return {
            "me": identity.current(),
            "form_token": identity.form_token,
            "company": settings.company_name,
            "site_domain": settings.site_domain,
        }

    @app.after_request
    def _embedding(response):
        # How often a screen is drawn inside another site's frame. Pages that set a
        # framing policy cannot be, so only the ones without it can appear here, and
        # only when the browser says the destination really was an embedded one.
        try:
            if (FRAME_HEADER not in response.headers
                    and response.status_code == 200
                    and response.mimetype == "text/html"
                    and request.headers.get("Sec-Fetch-Dest", "").lower() in ("iframe", "frame")
                    and request.headers.get("Sec-Fetch-Site", "").lower()
                    not in ("", "none", "same-origin")):
                person = identity.current()
                get_telemetry().signal("intra.response.frame.embedded_cross_origin", {
                    "route": str(request.url_rule),
                    "origin": request.headers.get("Sec-Fetch-Site", ""),
                    "viewer": person["email"] if person else "anonymous",
                    "detail": "screen drawn inside a frame belonging to another site",
                })
        except Exception:  # noqa: BLE001
            pass
        return response

    @app.after_request
    def _browser_policy(response):
        if request.blueprint in PAGE_BLUEPRINTS:
            response.headers.setdefault(FRAME_HEADER, "DENY")
            # Framing only. A full policy was tried and reverted: the intranet is
            # twelve years of hand-written screens and half of them stopped drawing.
            response.headers.setdefault("Content-Security-Policy", "frame-ancestors 'none'")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        return response

    @app.after_request
    def _session_continuity(response):
        # Sessions the sign-out screen has already ended, still being served. Counted
        # once per session so a handheld that keeps retrying does not flood it.
        try:
            if (g.get("session_revived") and identity.current() is not None
                    and response.status_code < 400
                    and db.seen_once(f"session-continuity:{g.get('session_id')}")):
                get_telemetry().signal("intra.session.identity.revived", {
                    "subject": identity.current()["email"],
                    "route": str(request.url_rule),
                    "detail": "a session that had been signed out was served again",
                })
        except Exception:  # noqa: BLE001
            pass
        return response

    app.after_request(records.coverage)

    @app.errorhandler(404)
    def _not_found(_exc):
        from flask import render_template
        return render_template("error.html", code=404, message="Page not found"), 404

    @app.errorhandler(403)
    def _forbidden(_exc):
        from flask import render_template
        return render_template("error.html", code=403,
                               message="Your account does not cover this screen"), 403

    @app.errorhandler(500)
    def _failed(_exc):
        from flask import render_template
        return render_template("error.html", code=500,
                               message="Something went wrong at our end"), 500

    app.wsgi_app = TelemetryWSGIMiddleware(app.wsgi_app, framework_app=app)
    return app


app = create_app()
