"""Provisioning: the whole corpus of content, derived from ``DEPLOY_SEED``.

The publication's content is generated rather than fixtured. Two installations with
different seeds share no names, no addresses, no slugs and no passphrases, which is
what keeps a copy of this service from looking like every other copy of it. Everything
is derived from one pseudo-random stream keyed on the seed, so the same seed always
produces byte-identical content: identifiers and timestamps do not move between runs,
which the reporting jobs that compare two periods rely on.

Run as a module to re-provision::

    python -m pressroom.seed              # provision and print the state digest
    python -m pressroom.seed --accounts   # also print the provisioned logins
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .settings import settings

# A fixed point in time so that "three weeks ago" means the same thing on every
# provisioning run. Content that drifts makes two reporting periods incomparable.
EPOCH = datetime(2026, 1, 6, 9, 0, 0)

SURNAMES = (
    "Arrigoni", "Bakare", "Venn", "Okonkwo", "Halloran", "Peruzzi", "Marchetti",
    "Sandoval", "Lindqvist", "Duchesne", "Rasmussen", "Kowalczyk", "Ferreira",
    "Nakamura", "Whitcombe", "Beaumont", "Osei", "Cardoso", "Mikkelsen", "Trenholm",
)
FORENAMES = (
    "Fenella", "Hakeem", "Sylvie", "Ngozi", "Declan", "Marta", "Ivo", "Rosa",
    "Anders", "Camille", "Bea", "Tomas", "Ines", "Kenji", "Ruth", "Oliver",
    "Adwoa", "Nuno", "Lars", "Iris",
)
PASSWORD_WORDS = (
    "harbour", "lantern", "sandstone", "meridian", "quarry", "thistle", "cormorant",
    "driftwood", "kestrel", "brambling", "saltmarsh", "gantry", "foreshore", "bracken",
    "windlass", "capstan", "estuary", "shingle", "trawler", "beacon",
)
TOPICS = (
    ("civic", "Civic affairs", "The council chamber, the committees and the money."),
    ("harbour", "Harbour", "Cargo, ferries, dredging and the people who work the quays."),
    ("transport", "Transport", "Buses, the branch line and everything that connects them."),
    ("culture", "Culture", "What is on, what closed, and who is keeping it going."),
    ("environment", "Environment", "Water quality, air quality and the coast itself."),
    ("business", "Business", "Trade, jobs and the local economy."),
    ("education", "Education", "Schools, the college and the apprenticeship pipeline."),
)
HEADLINE_SUBJECTS = (
    "the harbour vote", "the ferry timetable", "the branch line reopening",
    "the quayside redevelopment", "the bathing water report", "the college merger",
    "the market hall lease", "the dredging contract", "the flood defence budget",
    "the bus franchise", "the coastal path diversion", "the fishing quota talks",
    "the sixth form places", "the pier restoration", "the freeport bid",
    "the water company fine", "the town deal money", "the school streets plan",
    "the lifeboat station", "the winter tourism figures", "the allotment waiting list",
    "the museum funding round", "the recycling contract", "the seafront lighting",
)
HEADLINE_FORMS = (
    "What {subject} means for {audience}",
    "{subject_title}: the numbers behind the decision",
    "Inside {subject}",
    "{subject_title}, explained",
    "Why {subject} has stalled",
    "The paperwork trail behind {subject}",
    "{subject_title} and the questions nobody asked",
    "Five things to know about {subject}",
)
AUDIENCES = (
    "commuters", "the fishing fleet", "the west ward", "small traders",
    "families on the estate", "the college", "weekend visitors", "the ferry crews",
)
STANDFIRSTS = (
    "A decision taken in twenty minutes will shape the next fifteen years.",
    "The paperwork says one thing. The people who work there say another.",
    "We read every page of the report so you do not have to.",
    "It has been promised four times since 2011. Here is where it actually stands.",
    "The money is real, the timetable is not, and the difference matters.",
    "Nobody objected, which turns out to be the most interesting part.",
)
BODY_SENTENCES = (
    "The committee met on a Tuesday afternoon with four members present and one apology.",
    "Officers had recommended approval, and the recommendation ran to eleven pages.",
    "The figures in the appendix do not match the figures in the summary, and both are cited.",
    "Two objections were received during the consultation, neither of them from the ward.",
    "The contractor has held the framework since 2019 and has never been the lowest bid.",
    "A freedom of information request returned the same document with the costs removed.",
    "Residents were told the work would take eleven weeks. It has taken thirty-one.",
    "The harbour board says the dredging schedule is commercially confidential.",
    "There is no dispute about the survey. There is a dispute about what it means.",
    "The money comes from a fund that closes in March, which explains the timetable.",
    "We asked for the risk register and were sent the agenda instead.",
    "Nothing in the minutes records the discussion that everyone remembers having.",
)
COMMENT_SENTENCES = (
    "Good piece. The timetable point deserved more room.",
    "This is the first coverage that has actually read the appendix.",
    "I sat through that meeting and it was worse than this makes it sound.",
    "Does anyone know whether the consultation responses are published anywhere?",
    "The comparison with the 2011 scheme is the bit that stuck with me.",
    "My street has had the diversion for eight months now.",
    "Please keep following this one, it goes quiet every summer and then reappears.",
    "Small correction: the lease ran to 2027, not 2026.",
    "Thanks for putting the figures side by side, nobody else has.",
    "I work on the quay and none of us were asked.",
)
DISPLAY_SUFFIX = ("", "", "", " (subscriber)", "")
PLUGINS = (
    ("render-figures", "Charts and tables in article bodies."),
    ("render-timeline", "Chronology blocks for long-running stories."),
    ("wire-ingest", "Normalises wire copy into the studio's article shape."),
    ("caption-tools", "Caption and credit helpers for the picture desk."),
    ("archive-export", "Nightly export of published articles to the archive."),
    ("audience-reports", "Readership roll-ups for the weekly editorial meeting."),
)


# The date the address-and-code sign-in is meant to be withdrawn.
CODE_RETIREMENT = datetime(2031, 1, 1)


def stream(purpose: str) -> random.Random:
    """A pseudo-random stream keyed on the deployment seed and a purpose.

    One stream per purpose so that adding, say, another newsletter issue does not
    renumber every author.
    """
    key = hashlib.sha256(f"{settings().deploy_seed}|{purpose}".encode()).digest()
    return random.Random(int.from_bytes(key, "big"))


def slugify(text: str) -> str:
    out = []
    for ch in text.lower():
        if ch.isalnum():
            out.append(ch)
        elif out and out[-1] != "-":
            out.append("-")
    return "".join(out).strip("-")[:70]


def when(days: float) -> datetime:
    return EPOCH - timedelta(days=days)


def hash_password(password: str, salt: str) -> str:
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120_000)
    return f"pbkdf2_sha256$120000${salt}${base64.b64encode(digest).decode()}"


def _passphrase(rng: random.Random) -> str:
    return f"{rng.choice(PASSWORD_WORDS)}-{rng.choice(PASSWORD_WORDS)}-{rng.randrange(1000, 9999)}"


# --------------------------------------------------------------------- generation

def build() -> dict[str, list[dict[str, Any]]]:
    """Build every collection in memory. Pure: the same seed gives the same documents."""
    cfg = settings()
    domain = cfg.site_domain

    people = stream("people")
    names: list[tuple[str, str]] = []
    used: set[str] = set()
    for _ in range(24):
        for _attempt in range(40):
            pair = (people.choice(FORENAMES), people.choice(SURNAMES))
            handle = f"{pair[0][0].lower()}-{pair[1].lower()}"
            if handle not in used:
                used.add(handle)
                names.append(pair)
                break

    accounts: list[dict[str, Any]] = []
    authors: list[dict[str, Any]] = []
    roles = ["editor", "author", "author", "author", "author", "author",
             "author", "author", "reader", "reader", "reader", "reader"]
    secrets = stream("secrets")
    for index, role in enumerate(roles):
        forename, surname = names[index]
        handle = f"{forename[0].lower()}-{surname.lower()}"
        email = f"{forename[0].lower()}.{surname.lower()}@{domain}"
        account_id = f"usr-{1001 + index}"
        password = _passphrase(secrets)
        accounts.append({
            "_id": account_id,
            "email": email,
            "display": f"{forename} {surname}",
            "handle": handle,
            "role": role,
            "desk": "newsroom",
            "password_hash": hash_password(password, f"s{index:03d}"),
            "created": when(900 - index * 17),
            "status": "active",
            # The address-only sign-in the paper has offered since it dropped its
            # forum: the reader gets a code by email and types it in. The code is kept
            # on the account with the moment it stops being usable.
            "signin_code": hashlib.sha256(
                f"{settings().deploy_seed}|code|{index}".encode()).hexdigest()[:8].upper(),
            # The scheme retires with the last of the forum accounts; until then a
            # migrated reader's code keeps working.
            "code_expires": CODE_RETIREMENT,
            # Kept beside the account so provisioning can be replayed; never projected.
            "provisioned_passphrase": password,
        })
        if role in ("editor", "author"):
            authors.append({
                "_id": f"aut-{index:02d}",
                "account_id": account_id,
                "handle": handle,
                "display": f"{forename} {surname}",
                "role_title": "Managing editor" if role == "editor" else "Staff writer",
                "bio": f"{forename} writes about {TOPICS[index % len(TOPICS)][1].lower()} "
                       f"for the {cfg.site_name}.",
                "topics": [TOPICS[index % len(TOPICS)][0],
                           TOPICS[(index + 3) % len(TOPICS)][0]],
                "status": "active",
                "joined": when(880 - index * 21),
                # Held for the desk, never served on the public directory.
                "contact_email": f"{handle}@desk.{domain}",
                "direct_line": f"+44 1000 {700000 + index * 137:06d}"[:17],
                "day_rate": 180 + index * 15,
            })

    topics = [
        {"_id": f"tpc-{i:02d}", "slug": slug, "name": name, "description": desc}
        for i, (slug, name, desc) in enumerate(TOPICS)
    ]

    copy = stream("articles")
    articles: list[dict[str, Any]] = []
    subjects = list(HEADLINE_SUBJECTS)
    copy.shuffle(subjects)
    for index in range(28):
        subject = subjects[index % len(subjects)]
        form = HEADLINE_FORMS[index % len(HEADLINE_FORMS)]
        title = form.format(
            subject=subject,
            subject_title=subject[0].upper() + subject[1:],
            audience=copy.choice(AUDIENCES),
        )
        author = authors[index % len(authors)]
        topic = TOPICS[index % len(TOPICS)][0]
        published = when(300 - index * 9)
        paragraphs = [
            " ".join(copy.sample(BODY_SENTENCES, 4)) for _ in range(4)
        ]
        reference = int(hashlib.sha256(
            f"{settings().deploy_seed}|story|{index}".encode()).hexdigest(), 16) % 90000 + 10000
        articles.append({
            "_id": f"art-{index + 1:04d}",
            # The story reference the desk quotes in corrections and the archive
            # indexes on. It is part of the address, as it has been since the paper
            # was on paper.
            "reference": reference,
            "slug": f"{slugify(title)[:60]}-{reference}",
            "title": title,
            "standfirst": copy.choice(STANDFIRSTS),
            "body_html": "".join(f"<p>{p}</p>" for p in paragraphs),
            "word_count": sum(len(p.split()) for p in paragraphs),
            "topic": topic,
            "author_handle": author["handle"],
            "published_at": published,
            "year": published.year,
            "month": published.month,
            "status": "published" if index < 24 else "scheduled",
            "reads": 400 + copy.randrange(0, 9000),
            "tags": copy.sample([t[0] for t in TOPICS], 2),
        })

    talk = stream("comments")
    comments: list[dict[str, Any]] = []
    counter = 0
    for article in articles:
        if article["status"] != "published":
            continue
        for _ in range(talk.randrange(2, 7)):
            counter += 1
            forename, surname = names[talk.randrange(len(names))]
            body = talk.choice(COMMENT_SENTENCES)
            comments.append({
                "_id": f"cmt-{counter:04d}",
                "article_slug": article["slug"],
                "author_display": f"{forename} {surname[0]}."
                                  + talk.choice(DISPLAY_SUFFIX),
                "body_html": f"<p>{body}</p>",
                "created": article["published_at"] + timedelta(hours=talk.randrange(1, 90)),
                "status": "published",
                # The moderation console's own fields. They are on the same documents
                # as the public thread and are meant to stay out of its projection.
                "raw_body": body,
                "reporter_email": f"{forename[0].lower()}.{surname.lower()}@mailbox.example",
                "moderation": {"note": "auto-approved, no terms matched", "by": "queue"},
                "spam_score": round(talk.random() * 0.3, 3),
            })

    letters = stream("newsletter")
    issues = [
        {
            "_id": f"iss-{40 - i:03d}",
            "number": 40 - i,
            "subject": f"Issue {40 - i}: {articles[i]['title']}",
            "sent_at": when(14 * i + 3),
            "summary": articles[i]["standfirst"],
            "article_slugs": [a["slug"] for a in articles[i:i + 4]],
        }
        for i in range(6)
    ]
    subscribers = []
    for index in range(36):
        forename, surname = names[index % len(names)]
        subscribers.append({
            "_id": f"sub-{index + 1:04d}",
            "email": f"{forename.lower()}.{surname.lower()}{index}@mailbox.example",
            "token": "ntk-" + hashlib.sha256(
                f"{settings().deploy_seed}|sub|{index}".encode()).hexdigest()[:10],
            "topics": letters.sample([t[0] for t in TOPICS], 2),
            "cadence": letters.choice(["weekly", "weekly", "monthly"]),
            "confirmed": True,
            "created": when(500 - index * 9),
        })

    work = stream("drafts")
    drafts = []
    for author_index, author in enumerate(authors[:6]):
        for n in range(1, 4):
            subject = subjects[(author_index * 3 + n) % len(subjects)]
            drafts.append({
                "_id": f"dft-{2001 + author_index}-{n:02d}",
                "owner": author["handle"],
                "desk": "newsroom",
                "title": f"Working: {subject}",
                "body": " ".join(work.sample(BODY_SENTENCES, 3)),
                "state": "embargoed" if n == 1 else "in-progress",
                "embargo_until": when(-7 - n),
                "updated": when(20 - n * 2 + author_index),
            })

    pictures = stream("assets")
    assets = []
    for index in range(1, 61):
        article = articles[(index - 1) % len(articles)]
        assets.append({
            "_id": f"ast-{index:04d}",
            "filename": f"{article['slug'][:28]}-{index:04d}.png",
            "mime": "image/png",
            "width": 640,
            "height": 360,
            "credit": pictures.choice([
                "Picture desk", "Wirefeed North", "Harbour Press", "Contributed",
            ]),
            "article_slug": article["slug"],
            # The story's state decides whether the picture may be shown.
            "state": "published" if article["status"] == "published" else "held",
            "uploaded": article["published_at"] - timedelta(days=2),
        })

    plugins = [
        {
            "_id": f"plg-{i:02d}",
            "name": f"{settings().plugin_namespace}{name}",
            "version": f"1.{i}.{i * 3 % 7}",
            "summary": summary,
            "enabled": i < 4,
        }
        for i, (name, summary) in enumerate(PLUGINS)
    ]

    stats = [
        {
            "_id": f"sta-{a['_id'][4:]}",
            "slug": a["slug"],
            "reads": a["reads"],
            "finishes": int(a["reads"] * 0.41),
            "shares": int(a["reads"] * 0.03),
            "year": a["year"],
            "month": a["month"],
            "topic": a["topic"],
        }
        for a in articles
    ]

    return {
        "accounts": accounts,
        "authors": authors,
        "topics": topics,
        "articles": articles,
        "comments": comments,
        "issues": issues,
        "subscribers": subscribers,
        "drafts": drafts,
        "assets": assets,
        "plugins": plugins,
        "stats": stats,
    }


# --------------------------------------------------------------------- media files

def write_media(assets: list[dict[str, Any]]) -> None:
    """Write one small picture per asset. Deterministic bytes for a given seed."""
    from PIL import Image

    root = Path(settings().media_root)
    root.mkdir(parents=True, exist_ok=True)
    for existing in root.glob("*.png"):
        existing.unlink()
    tone = stream("media")
    for asset in assets:
        shade = 40 + tone.randrange(0, 160)
        image = Image.new("RGB", (asset["width"] // 4, asset["height"] // 4),
                          (shade, shade // 2 + 40, 200 - shade // 2))
        buffer = io.BytesIO()
        image.save(buffer, format="PNG", optimize=False, compress_level=6)
        (root / f"{asset['_id']}.png").write_bytes(buffer.getvalue())


# --------------------------------------------------------------------- provisioning

def _storable(document: dict[str, Any]) -> dict[str, Any]:
    """The document as it is written. The provisioning passphrase is not part of it:
    it exists so a run can be replayed from a terminal, and it has no business in a
    collection anything queries."""
    return {k: v for k, v in document.items() if k != "provisioned_passphrase"}


def digest_of(collections: dict[str, list[dict[str, Any]]]) -> str:
    payload = {
        name: [json.dumps(_storable(doc), sort_keys=True, default=str) for doc in docs]
        for name, docs in collections.items()
    }
    blob = json.dumps(payload, sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()[:16]


def provision(with_media: bool = True,
              content: dict[str, list[dict[str, Any]]] | None = None) -> tuple[str, dict[str, int]]:
    """Drop everything this service owns and write the seeded state back.

    Idempotent by construction: every collection is replaced, every cache key this
    service owns is removed, and both are rebuilt from the same pure function.
    """
    from .store import cache, database

    collections = content if content is not None else build()
    db = database()
    for name in list(collections) + ["recovery", "sessions"]:
        db.drop_collection(name)
    counts: dict[str, int] = {}
    for name, docs in collections.items():
        if docs:
            db[name].insert_many([_storable(doc) for doc in docs])
        counts[name] = len(docs)

    db["accounts"].create_index("email")
    db["articles"].create_index("slug")
    db["comments"].create_index("article_slug")
    db["assets"].create_index("state")

    kv = cache()
    for pattern in ("session:*", "preview:*", "recovery:*", "feed:*", "rate:*",
                    "report:*"):
        keys = list(kv.scan_iter(match=pattern, count=500))
        if keys:
            kv.delete(*keys)
    # Share links the studio has already handed out. Everything the service issues
    # afterwards joins this set.
    issued = [f"pv-{i:04d}" for i in range(1, 9)]
    kv.sadd("preview:issued", *issued)

    if with_media:
        write_media(collections["assets"])

    return digest_of(collections), counts


def main(argv: list[str]) -> int:
    from .store import database

    if "--if-empty" in argv:
        # A restart must not cost the publication its content. Provisioning only runs
        # against a store that has none.
        if database()["articles"].estimated_document_count() > 0:
            counts = {name: database()[name].estimated_document_count()
                      for name in build()}
            print(f"state {digest_of(build())} documents={sum(counts.values())} "
                  + " ".join(f"{n}={c}" for n, c in sorted(counts.items())))
            return 0
    digest, counts = provision()
    total = sum(counts.values())
    summary = " ".join(f"{name}={count}" for name, count in sorted(counts.items()))
    print(f"state {digest} documents={total} {summary}")
    if "--accounts" in argv:
        for account in build()["accounts"]:
            print(f"  {account['role']:<7} {account['email']} "
                  f"{account['provisioned_passphrase']} {account['_id']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
