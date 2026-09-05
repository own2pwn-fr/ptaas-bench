"""Lay the estate back down exactly as it was deployed.

One routine, run from the operations listener, that rebuilds every document root from
source, rewrites the leftovers the deployments left behind, reloads the datastores and
prints a digest of the result. Running it twice must produce the same digest; if it does
not, something is still holding state from a previous deployment and the next set of
figures taken off this estate would not be comparable with the last.

Order matters in two places. The archive of the document root is taken after the site is
written and before the media directory is filled, because the media directory is a
separate mount and was never inside the archive. The datastores are loaded last, because
they are the slowest to come up and the only step that can fail for a reason outside
this host.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from . import artefacts, gitdir, pages, stores, svndir
from .context import build_context

# Fixed points in the estate's own history. They are literals rather than clock reads so
# that a rebuild does not move a single byte.
DEPLOY_EPOCH = 1_752_288_000          # 12 July 2026, the release the archive predates
ARCHIVE_NAME = "wwwroot-preflight-20260712.tar.gz"
PORTAL_REVISION = 148


@dataclass
class SeededState:
    """What the estate holds, and the measurements the counters are read against."""

    digest: str = ""
    # Size of the generated listing of the media directory, measured on this deployment.
    listing_bytes: int = 0
    listing_measured: bool = False
    # Documents whose transfer is worth counting: URL path -> size on disk.
    file_sizes: dict[str, int] = field(default_factory=dict)
    # Repository metadata under the document root, split into the two halves a
    # reconstruction needs.
    git_content_urls: set[str] = field(default_factory=set)
    git_listing_urls: set[str] = field(default_factory=set)
    svn_content_urls: set[str] = field(default_factory=set)
    svn_listing_urls: set[str] = field(default_factory=set)
    # Length of an answer from the search cluster that found nothing, so that an answer
    # carrying rows can be told from one that does not.
    search_empty_bytes: int = 512
    manifests: dict = field(default_factory=dict)


def _write_tree(root: str, files: dict[str, bytes]) -> list[tuple[str, bytes]]:
    written = []
    for relative in sorted(files):
        path = os.path.join(root, relative)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(files[relative])
        os.chmod(path, 0o644)
        written.append((relative, files[relative]))
    return written


def _reset_dir(path: str) -> None:
    """Empty a directory in place, keeping the directory itself.

    In place rather than removed and recreated: the document roots and the password
    file's directory are separate mounts on this host, and a mount point cannot be
    unlinked from inside the container that holds it -- the attempt fails with
    "resource busy" and takes the whole deployment with it.
    """
    os.makedirs(path, mode=0o755, exist_ok=True)
    for name in os.listdir(path):
        full = os.path.join(path, name)
        if os.path.isdir(full) and not os.path.islink(full):
            shutil.rmtree(full)
        else:
            os.unlink(full)


def _tracked_source(ctx, site: dict[str, bytes]) -> dict[str, bytes]:
    """The files the site repository actually tracks: pages, styles, scripts, notes."""
    keep = ("index.html", "about.html", "services.html", "capabilities.html",
            "projects.html", "contact.html", "careers.html", "robots.txt",
            "assets/css/site.css", "assets/js/site.js")
    tracked = {name: site[name] for name in keep if name in site}
    tracked["deploy/notes.txt"] = (
        "Deployment\n"
        "==========\n\n"
        "The site is pulled onto the web host and served from the working copy.\n"
        "There is no build step: edit, commit, push, then pull on the host.\n\n"
        f"  ssh deploy@web01.mgmt.{ctx.domain}\n"
        "  cd /srv/sites/www && git pull --ff-only\n\n"
        "Media files are not tracked here. They live on the media mount and are\n"
        "uploaded over SFTP by the sales office.\n"
    ).encode()
    return tracked


def _history(ctx, tracked: dict[str, bytes]) -> list[dict]:
    """Eight commits, ending with the working copy the host is serving."""
    early = dict(tracked)
    # The environment file of the old content system was committed while the rebuild was
    # under way and taken out again once the export was working.
    early["deploy/prod.env"] = artefacts.environment_file(ctx)

    first = {name: tracked[name] for name in ("index.html", "assets/css/site.css")
             if name in tracked}
    second = {name: tracked[name] for name in
              ("index.html", "about.html", "services.html", "assets/css/site.css")
              if name in tracked}
    third = dict(second)
    third["deploy/prod.env"] = early["deploy/prod.env"]
    fourth = dict(early)
    fifth = dict(early)
    sixth = dict(tracked)          # the environment file is taken back out here
    seventh = dict(tracked)
    day = 86_400
    return [
        {"message": "Initial import of the new site", "author": 4,
         "when": DEPLOY_EPOCH - 340 * day, "files": first},
        {"message": "About and services pages, shared header", "author": 4,
         "when": DEPLOY_EPOCH - 322 * day, "files": second},
        {"message": "Add settings for the export from the old editor", "author": 6,
         "when": DEPLOY_EPOCH - 300 * day, "files": third},
        {"message": "Capabilities, projects and contact pages", "author": 4,
         "when": DEPLOY_EPOCH - 288 * day, "files": fourth},
        {"message": "Careers page and the agency's portal link", "author": 2,
         "when": DEPLOY_EPOCH - 210 * day, "files": fifth},
        {"message": "Remove the old editor settings now the export is done", "author": 6,
         "when": DEPLOY_EPOCH - 120 * day, "files": sixth},
        {"message": "Correct the works address and the phone number", "author": 2,
         "when": DEPLOY_EPOCH - 46 * day, "files": seventh},
        {"message": "Price list link, robots and sitemap for the new pages", "author": 4,
         "when": DEPLOY_EPOCH - 9 * day, "files": dict(tracked)},
    ]


def _static_decoys(ctx) -> dict[str, bytes]:
    """Files on the assets host that the host's own rules refuse to serve.

    They are here because they are the same leftovers, on a host where the deny rules
    were applied. Their presence is what makes the rules worth having.
    """
    repository = gitdir.build(
        ctx,
        [{"message": "Shared assets, first cut", "author": 5,
          "when": DEPLOY_EPOCH - 400 * 86_400,
          "files": {"README.txt": b"Shared assets for the estate.\n"}},
         {"message": "Add the print stylesheet", "author": 5,
          "when": DEPLOY_EPOCH - 60 * 86_400,
          "files": {"README.txt": b"Shared assets for the estate.\n",
                    "print.css": b"@media print { nav, footer { display: none; } }\n"}}],
        remote=f"git@git.mgmt.{ctx.domain}:web/assets.git",
        description="Shared assets for the estate's sites.",
    )
    files = {f".git/{name}": body for name, body in repository.files.items()}
    files[".env"] = (
        "ASSET_BUCKET=assets\n"
        f"ASSET_ORIGIN=https://{ctx.static_host}\n"
        f"PURGE_TOKEN={ctx.token('assets/purge', 32)}\n"
    ).encode()
    files["vendor/backup/assets-2026-05-30.tar.gz"] = artefacts.archive(
        {"print.css": b"@media print { nav { display: none; } }\n"},
        prefix="assets", mtime=DEPLOY_EPOCH - 43 * 86_400)
    files["vendor/backup/bundle.js.bak"] = (
        "/* previous bundle, kept until the new one has been live a month */\n"
        "window.northlake=window.northlake||{};\n"
    ).encode()
    files["vendor/backup/assets.sql"] = (
        "-- inventory of the asset host, exported for the audit\n"
        "CREATE TABLE assets (path text primary key, bytes integer);\n"
    ).encode()
    return files


def _docs_decoys(ctx) -> dict[str, bytes]:
    repository = gitdir.build(
        ctx,
        [{"message": "Handbook, imported from the shared drive", "author": 3,
          "when": DEPLOY_EPOCH - 500 * 86_400,
          "files": {"README.txt": b"Staff handbook source.\n"}}],
        remote=f"git@git.mgmt.{ctx.domain}:web/handbook.git",
        description="Staff handbook.",
    )
    files = {f".git/{name}": body for name, body in repository.files.items()}
    files[".env.production"] = (
        f"HANDBOOK_ORIGIN=https://{ctx.docs_host}\n"
        f"SEARCH_HOST=http://search.{ctx.domain}:9200\n"
        f"SEARCH_TOKEN={ctx.token('docs/search', 32)}\n"
    ).encode()
    # The maintained copy of the ordering interface, behind the operator login.
    files["api/openapi.json"] = _maintained_description()
    return files


def _maintained_description() -> bytes:
    """The documentation host serves the current description as an object.

    Written out directly rather than converted from the other format, because carrying a
    parser for one file would be the only reason this host needed one.
    """
    return json.dumps({
        "openapi": "3.0.3",
        "info": {"title": "Works Ordering API", "version": "2.4.1"},
        "paths": {
            "/orders": {"get": {"summary": "List works orders"},
                        "post": {"summary": "Raise a works order"}},
            "/orders/{orderId}": {"get": {"summary": "One order"}},
            "/customers": {"get": {"summary": "List customers"}},
            "/admin/rates": {"put": {"summary": "Replace the current rate card"}},
            "/admin/users": {"get": {"summary": "List accounts on the works system"}},
        },
        "note": "Maintained copy. Access is limited to the operations account.",
    }, indent=2).encode()


def _measure_listing(base_url: str, path: str, timeout: float = 5.0) -> int:
    request = urllib.request.Request(base_url.rstrip("/") + path, method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return len(response.read())


def _measure_empty_search(base: str, index: str, timeout: float = 10.0) -> int:
    query = json.dumps({"query": {"term": {"email": "no-such-address"}}}).encode()
    request = urllib.request.Request(f"{base}/{index}/_search", data=query, method="POST")
    request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return len(response.read())


def deploy(settings) -> SeededState:
    """Rebuild everything and return the state the counters are measured against."""
    ctx = build_context(settings.deploy_seed, settings.site_domain)
    state = SeededState()
    hashed: list[str] = []

    def account(prefix: str, written: list[tuple[str, bytes]]) -> None:
        for relative, body in written:
            hashed.append(f"{prefix}/{relative}:{hashlib.sha256(body).hexdigest()}")

    www_root = os.path.join(settings.sites_root, "www")
    static_root = os.path.join(settings.sites_root, "static")
    docs_root = os.path.join(settings.sites_root, "docs")
    for root in (www_root, static_root, docs_root, settings.private_root):
        _reset_dir(root)

    # -- the public site ----------------------------------------------------
    site = pages.build_www(ctx)
    site[".well-known/security.txt"] = pages.security_txt(ctx)
    account("www", _write_tree(www_root, site))

    leftovers: dict[str, bytes] = {
        ".env": artefacts.environment_file(ctx),
        "dump.sql.gz": artefacts.database_dump(ctx, mtime=DEPLOY_EPOCH - 300 * 86_400),
        "api-docs/index.html": artefacts.api_index_page(ctx),
        "api-docs/openapi.yaml": artefacts.api_description(ctx),
    }
    account("www", _write_tree(www_root, leftovers))
    for name in (".env", "dump.sql.gz", "api-docs/openapi.yaml"):
        state.file_sizes["/" + name] = len(leftovers[name])

    tracked = _tracked_source(ctx, site)
    repository = gitdir.build(
        ctx, _history(ctx, tracked),
        remote=f"git@git.mgmt.{ctx.domain}:web/site.git",
        description=f"{ctx.company} public website.",
    )
    account("www", _write_tree(www_root, {f".git/{name}": body
                                          for name, body in repository.files.items()}))
    state.git_content_urls = {f"/.git/{name}" for name in repository.blob_paths}
    state.git_listing_urls = {f"/.git/{name}" for name in repository.listing_paths}

    # -- the recruitment micro-site the agency delivered ---------------------
    portal_tracked = {name[len("careers/portal/"):]: body
                      for name, body in site.items()
                      if name.startswith("careers/portal/")}
    portal_tracked.setdefault("README.txt",
                              b"Recruitment micro-site. Delivered by the agency.\n")
    portal_tracked["deploy/credentials.txt"] = (
        "Handover notes\n"
        "==============\n\n"
        f"SFTP: portal@web01.mgmt.{ctx.domain}\n"
        f"Password: {ctx.passphrase('portal/sftp')}\n"
        f"Applicant mailbox: careers@{ctx.domain} / {ctx.passphrase('portal/mailbox')}\n"
        f"Form endpoint key: {ctx.token('portal/form-key', 32)}\n"
    ).encode()
    working_copy = svndir.build(
        ctx, portal_tracked,
        repository=f"svn://svn.{ctx.pick('portal/agency', ('bridgeway', 'quaymark', 'lindenrow'))}.co.uk/clients/northlake/portal",
        revision=PORTAL_REVISION,
        author=ctx.pick("portal/author", ("j.hollins", "m.arkwright", "t.beswick")),
        changed=(DEPLOY_EPOCH - 190 * 86_400) * 1_000_000,
    )
    account("www", _write_tree(www_root, {f"careers/portal/.svn/{name}": body
                                          for name, body in working_copy.files.items()}))
    state.svn_content_urls = {f"/careers/portal/.svn/{name}"
                              for name in working_copy.content_paths}
    state.svn_listing_urls = {f"/careers/portal/.svn/{name}"
                              for name in working_copy.listing_paths}

    # -- the archive somebody took before the July release -------------------
    members: dict[str, bytes] = {}
    for directory, _, names in os.walk(www_root):
        for name in names:
            full = os.path.join(directory, name)
            relative = os.path.relpath(full, www_root)
            if relative.startswith("media" + os.sep):
                continue          # a separate mount; it was never inside the archive
            with open(full, "rb") as handle:
                members[relative.replace(os.sep, "/")] = handle.read()
    archive = artefacts.archive(members, prefix="www", mtime=DEPLOY_EPOCH - 2 * 86_400)

    media = artefacts.media_library(ctx)
    media[ARCHIVE_NAME] = archive
    account("www", _write_tree(os.path.join(www_root, "media"), media))
    state.file_sizes["/media/" + ARCHIVE_NAME] = len(archive)

    # -- the two hosts that were built from the hardened template ------------
    account("static", _write_tree(static_root, pages.build_static(ctx)))
    account("static", _write_tree(static_root, _static_decoys(ctx)))
    account("docs", _write_tree(docs_root, pages.build_docs(ctx)))
    account("docs", _write_tree(docs_root, _docs_decoys(ctx)))

    account("private", _write_tree(settings.private_root, {
        "operators": artefacts.htpasswd([
            ("operations", ctx.passphrase("docs/operations"), ctx.token("docs/salt1", 8)),
            (ctx.person(1).handle, ctx.passphrase("docs/estimator"),
             ctx.token("docs/salt2", 8)),
        ]),
    }))

    # -- the datastores ------------------------------------------------------
    state.manifests["cache"] = stores.load_cache(
        ctx, settings.cache_host, settings.cache_port)
    state.manifests["queue"] = stores.load_queue(
        ctx, settings.queue_host, settings.queue_port)
    if settings.sessions_password:
        state.manifests["sessions"] = stores.load_sessions(
            ctx, settings.sessions_host, settings.sessions_port, settings.sessions_password)
    if settings.jobs_password:
        state.manifests["jobs"] = stores.load_sessions(
            ctx, settings.jobs_host, settings.jobs_port, settings.jobs_password)
    state.manifests["records"] = stores.load_records(
        ctx, settings.records_host, settings.records_port, settings.records_db)
    state.manifests["search"] = stores.load_search(
        ctx, settings.search_base, settings.search_index, settings.search_notes_index)

    # -- measurements --------------------------------------------------------
    try:
        state.listing_bytes = _measure_listing(settings.site_base_url, "/media/")
        state.listing_measured = True
    except (urllib.error.URLError, OSError, ValueError):
        # The web tier is not answering yet. Fall back to the shape of the listing: two
        # occurrences of every name plus the frame around them.
        state.listing_bytes = 2 * sum(len(name) for name in media) + 220
        state.listing_measured = False
    try:
        state.search_empty_bytes = _measure_empty_search(
            settings.search_base, settings.search_index)
    except (urllib.error.URLError, OSError, ValueError, KeyError):
        state.search_empty_bytes = 512

    hashed.append("stores:" + json.dumps(state.manifests, sort_keys=True))
    state.digest = hashlib.sha256("\n".join(sorted(hashed)).encode()).hexdigest()[:32]
    return state


def digest_line(state: SeededState) -> str:
    return f"state {state.digest}"


def wait_until_ready(settings, seconds: float = 300.0) -> None:
    """Block until the site answers, so the first deployment does not race the server."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(settings.site_base_url + "/", timeout=3):
                return
        except (urllib.error.HTTPError,):
            return
        except (urllib.error.URLError, OSError):
            time.sleep(0.5)
