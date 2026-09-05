"""Files that are not pages: downloads, the API description, credentials, archives.

Everything the estate publishes besides the site itself is written here, so that the
setup routine has one place to look when a document has to be replaced. Sizes are
modest on purpose -- the media library on this host is a convenience for the sales team,
not a document store.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import tarfile

ITOA64 = "./0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


# ---------------------------------------------------------------------------
# small binary documents
# ---------------------------------------------------------------------------

def pdf(title: str, lines: list[str]) -> bytes:
    """A single-page document, written out by hand rather than by a library."""
    body_lines = ["BT", "/F1 16 Tf", "72 760 Td", f"({_pdf_escape(title)}) Tj", "/F1 11 Tf"]
    for line in lines:
        body_lines.append("0 -18 Td")
        body_lines.append(f"({_pdf_escape(line)}) Tj")
    body_lines.append("ET")
    stream = "\n".join(body_lines).encode("latin-1", "replace")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, payload in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + payload + b"\nendobj\n"
    start = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n"
            f"{start}\n%%EOF\n").encode()
    return bytes(out)


def _pdf_escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def archive(members: dict[str, bytes], *, prefix: str, mtime: int) -> bytes:
    """A deterministic compressed archive: same input, same bytes."""
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.GNU_FORMAT) as tar:
        for name in sorted(members):
            info = tarfile.TarInfo(f"{prefix}/{name}" if prefix else name)
            info.size = len(members[name])
            info.mtime = mtime
            info.mode = 0o644
            info.uid = 1001
            info.gid = 1001
            info.uname = "deploy"
            info.gname = "deploy"
            tar.addfile(info, io.BytesIO(members[name]))
    compressed = io.BytesIO()
    with gzip.GzipFile(fileobj=compressed, mode="wb", compresslevel=6, mtime=mtime) as zipped:
        zipped.write(raw.getvalue())
    return compressed.getvalue()


def gzip_bytes(payload: bytes, *, mtime: int) -> bytes:
    out = io.BytesIO()
    with gzip.GzipFile(fileobj=out, mode="wb", compresslevel=6, mtime=mtime) as zipped:
        zipped.write(payload)
    return out.getvalue()


# ---------------------------------------------------------------------------
# credentials
# ---------------------------------------------------------------------------

def apr1(password: str, salt: str) -> str:
    """The password hash the web server's own utility writes, computed here instead.

    Implemented rather than shelled out to, because the deployment host does not carry
    the server's tools -- only the server.
    """
    pwd = password.encode()
    salt_bytes = salt.encode()[:8]
    digest = hashlib.md5(pwd + b"$apr1$" + salt_bytes)
    alternate = hashlib.md5(pwd + salt_bytes + pwd).digest()
    remaining = len(pwd)
    while remaining > 0:
        digest.update(alternate[:min(remaining, 16)])
        remaining -= 16
    remaining = len(pwd)
    while remaining:
        digest.update(b"\0" if remaining & 1 else pwd[:1])
        remaining >>= 1
    final = digest.digest()
    for index in range(1000):
        step = hashlib.md5()
        step.update(pwd if index & 1 else final)
        if index % 3:
            step.update(salt_bytes)
        if index % 7:
            step.update(pwd)
        step.update(final if index & 1 else pwd)
        final = step.digest()

    encoded = ""
    for a, b, c in ((0, 6, 12), (1, 7, 13), (2, 8, 14), (3, 9, 15), (4, 10, 5)):
        value = (final[a] << 16) | (final[b] << 8) | final[c]
        for _ in range(4):
            encoded += ITOA64[value & 0x3F]
            value >>= 6
    value = final[11]
    for _ in range(2):
        encoded += ITOA64[value & 0x3F]
        value >>= 6
    return f"$apr1${salt}${encoded}"


def htpasswd(entries: list[tuple[str, str, str]]) -> bytes:
    """``entries`` is (user, password, salt)."""
    return "".join(f"{user}:{apr1(password, salt)}\n" for user, password, salt in entries).encode()


# ---------------------------------------------------------------------------
# the environment file the content system was configured with
# ---------------------------------------------------------------------------

def environment_file(ctx) -> bytes:
    """Configuration of the content system this site was exported from.

    Kept beside the export so that the next rebuild starts from the same settings.
    """
    key = ctx.token("app/key", 43)
    return (
        f'APP_NAME="{ctx.company}"\n'
        "APP_ENV=production\n"
        f"APP_KEY=base64:{key}=\n"
        "APP_DEBUG=false\n"
        f"APP_URL=https://{ctx.www_host}\n"
        "\n"
        "LOG_CHANNEL=daily\n"
        "LOG_LEVEL=warning\n"
        "\n"
        "DB_CONNECTION=pgsql\n"
        f"DB_HOST=db01.mgmt.{ctx.domain}\n"
        "DB_PORT=5432\n"
        "DB_DATABASE=nlf_site\n"
        "DB_USERNAME=nlf_site\n"
        f"DB_PASSWORD={ctx.passphrase('site/postgres')}\n"
        "\n"
        "CACHE_DRIVER=redis\n"
        "SESSION_DRIVER=redis\n"
        f"REDIS_HOST=cache.{ctx.domain}\n"
        "REDIS_PORT=6379\n"
        "REDIS_PASSWORD=null\n"
        "\n"
        f"SESSION_REDIS_HOST=sessions.mgmt.{ctx.domain}\n"
        f"SESSION_REDIS_PASSWORD={ctx.passphrase('sessions/redis')}\n"
        "\n"
        f"QUEUE_CONNECTION=redis\n"
        f"QUEUE_REDIS_HOST=queue.mgmt.{ctx.domain}\n"
        f"QUEUE_REDIS_PASSWORD={ctx.passphrase('queue/redis')}\n"
        "\n"
        f"MONGO_DSN=mongodb://records.{ctx.domain}:27017/nlf_records\n"
        f"ELASTIC_HOST=http://search.{ctx.domain}:9200\n"
        "ELASTIC_INDEX=nlf-enquiries\n"
        "\n"
        "MAIL_MAILER=smtp\n"
        f"MAIL_HOST=mail.{ctx.domain}\n"
        "MAIL_PORT=587\n"
        f"MAIL_USERNAME=site@{ctx.domain}\n"
        f"MAIL_PASSWORD={ctx.passphrase('site/smtp')}\n"
        "MAIL_ENCRYPTION=tls\n"
        f'MAIL_FROM_ADDRESS="enquiries@{ctx.domain}"\n'
        "\n"
        f"FORMS_ENDPOINT=https://forms.{ctx.domain}/v1/enquiry\n"
        f"FORMS_SECRET={ctx.token('forms/secret', 40)}\n"
        f"SAGE_EXPORT_TOKEN={ctx.token('sage/token', 32)}\n"
    ).encode()


# ---------------------------------------------------------------------------
# the dump taken before the rebuild
# ---------------------------------------------------------------------------

def database_dump(ctx, *, mtime: int) -> bytes:
    """A dump of the content database, in the plain format the tool writes by default."""
    header = (
        "--\n"
        "-- PostgreSQL database dump\n"
        "--\n\n"
        "-- Dumped from database version 14.9\n"
        "-- Dumped by pg_dump version 14.9\n\n"
        "SET statement_timeout = 0;\n"
        "SET lock_timeout = 0;\n"
        "SET client_encoding = 'UTF8';\n"
        "SET standard_conforming_strings = on;\n"
        "SET check_function_bodies = false;\n"
        "SET row_security = off;\n\n"
        "SET default_tablespace = '';\n"
        "SET default_table_access_method = heap;\n\n"
        "--\n-- Name: users; Type: TABLE; Schema: public; Owner: nlf_site\n--\n\n"
        "CREATE TABLE public.users (\n"
        "    id integer NOT NULL,\n"
        "    name character varying(120) NOT NULL,\n"
        "    email character varying(160) NOT NULL,\n"
        "    password character varying(255) NOT NULL,\n"
        "    role character varying(32) DEFAULT 'editor'::character varying NOT NULL,\n"
        "    remember_token character varying(100),\n"
        "    created_at timestamp(0) without time zone,\n"
        "    updated_at timestamp(0) without time zone\n"
        ");\n\n"
        "ALTER TABLE public.users OWNER TO nlf_site;\n\n"
        "--\n-- Data for Name: users; Type: TABLE DATA; Schema: public; Owner: nlf_site\n--\n\n"
        "COPY public.users (id, name, email, password, role, remember_token, created_at, "
        "updated_at) FROM stdin;\n"
    )
    rows = []
    for index in range(8):
        person = ctx.person(index)
        digest = ctx.hexname(f"dump/user/{index}", 22)
        role = "admin" if index == 0 else ("editor" if index % 2 else "author")
        rows.append(
            f"{index + 1}\t{person.name}\t{person.email}\t"
            f"$2y$10${digest}{ctx.hexname(f'dump/user/{index}/tail', 31)}\t{role}\t\\N\t"
            f"2024-0{index % 8 + 1}-1{index % 9} 09:1{index % 6}:0{index % 9}\t"
            f"2026-0{index % 6 + 1}-2{index % 8} 14:2{index % 5}:1{index % 8}\n"
        )
    middle = (
        "\\.\n\n\n"
        "--\n-- Name: pages; Type: TABLE; Schema: public; Owner: nlf_site\n--\n\n"
        "CREATE TABLE public.pages (\n"
        "    id integer NOT NULL,\n"
        "    slug character varying(160) NOT NULL,\n"
        "    title character varying(200) NOT NULL,\n"
        "    body text,\n"
        "    published boolean DEFAULT false NOT NULL,\n"
        "    updated_at timestamp(0) without time zone\n"
        ");\n\n"
        "COPY public.pages (id, slug, title, body, published, updated_at) FROM stdin;\n"
    )
    page_rows = []
    slugs = ("index", "about", "services", "capabilities", "projects", "contact",
             "careers", "legal/privacy", "legal/terms")
    for index, slug in enumerate(slugs, start=1):
        page_rows.append(
            f"{index}\t{slug}\t{slug.replace('/', ' ').title()}\t"
            f"<p>Content held in the editor for the {slug} page.</p>\tt\t"
            f"2026-0{index % 6 + 1}-1{index % 9} 11:0{index % 6}:00\n"
        )
    footer = (
        "\\.\n\n\n"
        "--\n-- Name: enquiries; Type: TABLE; Schema: public; Owner: nlf_site\n--\n\n"
        "CREATE TABLE public.enquiries (\n"
        "    id integer NOT NULL,\n"
        "    company character varying(160),\n"
        "    contact character varying(160),\n"
        "    email character varying(160),\n"
        "    telephone character varying(40),\n"
        "    message text,\n"
        "    received_at timestamp(0) without time zone\n"
        ");\n\n"
        "COPY public.enquiries (id, company, contact, email, telephone, message, "
        "received_at) FROM stdin;\n"
    )
    enquiry_rows = []
    firms = ("Harker Plant", "Deeside Marine", "Ravensworth Civils", "Pellet & Sons",
             "Coldstream Rail", "Bexley Aggregates", "Trent Valley Cranes",
             "Kirkgate Developments", "Ossett Precast", "Marston Wharf",
             "Ellerby Groundworks", "Sandal Bridge Works")
    for index, firm in enumerate(firms, start=1):
        person = ctx.person(index)
        enquiry_rows.append(
            f"{index}\t{firm}\t{person.name}\t"
            f"{person.first.lower()}@{firm.split()[0].lower()}.co.uk\t"
            f"01{ctx.number(f'dump/tel/{index}', 100000000, 999999999)}\t"
            f"Enquiry about {ctx.pick(f'dump/subject/{index}', ('handrail', 'walkway', 'platform', 'stair', 'gantry'))} "
            f"fabrication, roughly {ctx.number(f'dump/tonnes/{index}', 2, 40)} tonnes.\t"
            f"2026-0{index % 6 + 1}-0{index % 8 + 1} 0{index % 8 + 1}:3{index % 6}:00\n"
        )
    tail = (
        "\\.\n\n\n"
        "--\n-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: nlf_site\n--\n\n"
        "ALTER TABLE ONLY public.users ADD CONSTRAINT users_pkey PRIMARY KEY (id);\n\n"
        "ALTER TABLE ONLY public.pages ADD CONSTRAINT pages_pkey PRIMARY KEY (id);\n\n"
        "ALTER TABLE ONLY public.enquiries ADD CONSTRAINT enquiries_pkey PRIMARY KEY (id);\n\n"
        "--\n-- PostgreSQL database dump complete\n--\n\n"
    )
    payload = (header + "".join(rows) + middle + "".join(page_rows) + footer
               + "".join(enquiry_rows) + tail).encode()
    return gzip_bytes(payload, mtime=mtime)


# ---------------------------------------------------------------------------
# the description of the ordering service
# ---------------------------------------------------------------------------

def api_description(ctx) -> bytes:
    return (
        "openapi: 3.0.3\n"
        "info:\n"
        f"  title: {ctx.company_short} Works Ordering API\n"
        "  version: 2.4.1\n"
        "  description: >\n"
        "    Ordering, dispatch and delivery-note service used by the works system and by\n"
        "    the partner portal. Not for public use; requests are rejected outside the\n"
        "    works network.\n"
        "  contact:\n"
        f"    name: {ctx.person(1).name}\n"
        f"    email: {ctx.person(1).email}\n"
        "servers:\n"
        f"  - url: http://works-api.mgmt.{ctx.domain}:8080/v2\n"
        "    description: works network\n"
        f"  - url: http://works-api.staging.{ctx.domain}:8080/v2\n"
        "    description: staging\n"
        "paths:\n"
        "  /orders:\n"
        "    get:\n"
        "      summary: List works orders\n"
        "      parameters:\n"
        "        - {name: status, in: query, schema: {type: string, enum: [draft, released, dispatched]}}\n"
        "        - {name: customer, in: query, schema: {type: string}}\n"
        "        - {name: page, in: query, schema: {type: integer, default: 1}}\n"
        "      responses:\n"
        "        '200': {description: A page of orders}\n"
        "    post:\n"
        "      summary: Raise a works order\n"
        "      responses:\n"
        "        '201': {description: Created}\n"
        "  /orders/{orderId}:\n"
        "    get:\n"
        "      summary: One order\n"
        "      parameters:\n"
        "        - {name: orderId, in: path, required: true, schema: {type: string}}\n"
        "      responses:\n"
        "        '200': {description: The order}\n"
        "        '404': {description: No such order}\n"
        "  /orders/{orderId}/dispatch:\n"
        "    post:\n"
        "      summary: Mark an order dispatched and print the delivery note\n"
        "      responses:\n"
        "        '202': {description: Queued for printing}\n"
        "  /customers:\n"
        "    get:\n"
        "      summary: List customers\n"
        "      responses:\n"
        "        '200': {description: A page of customers}\n"
        "  /deliveries/{noteId}/pdf:\n"
        "    get:\n"
        "      summary: Delivery note as a document\n"
        "      responses:\n"
        "        '200': {description: The document}\n"
        "  /admin/rates:\n"
        "    put:\n"
        "      summary: Replace the current rate card\n"
        "      description: Restricted to the estimating team.\n"
        "      responses:\n"
        "        '204': {description: Replaced}\n"
        "  /admin/users:\n"
        "    get:\n"
        "      summary: List accounts on the works system\n"
        "      responses:\n"
        "        '200': {description: The accounts}\n"
        "components:\n"
        "  securitySchemes:\n"
        "    worksToken:\n"
        "      type: http\n"
        "      scheme: bearer\n"
        "      bearerFormat: JWT\n"
        "security:\n"
        "  - worksToken: []\n"
    ).encode()


def api_index_page(ctx) -> bytes:
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en-GB">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>Works Ordering API &mdash; {ctx.company}</title>\n"
        '<meta name="description" content="Interface description for the works ordering '
        'service.">\n'
        '<meta name="robots" content="noindex">\n'
        f'<link rel="stylesheet" href="http://{ctx.static_host}/assets/css/site.css">\n'
        "</head>\n<body>\n"
        "<main>\n"
        "<h1>Works Ordering API</h1>\n"
        "<p>Version 2.4.1. The description below is the one the partner portal was built\n"
        "against. It is kept here while the integration is signed off.</p>\n"
        '<p><a href="openapi.yaml">openapi.yaml</a></p>\n'
        f"<p>Questions to {ctx.person(1).name}, {ctx.person(1).email}.</p>\n"
        "</main>\n</body>\n</html>\n"
    ).encode()


# ---------------------------------------------------------------------------
# the media library
# ---------------------------------------------------------------------------

def media_library(ctx) -> dict[str, bytes]:
    """Documents the sales team drop here by hand, outside the site build."""
    return {
        "capability-statement-2026.pdf": pdf(
            f"{ctx.company} \u2014 Capability Statement {ctx.year}",
            [
                "Structural steel fabrication and site erection.",
                f"Works: {ctx.city}. Execution class EXC3 to BS EN 1090-2.",
                "Handrails, walkways, access platforms and stairs.",
                "Protective coatings to a specified system.",
                "Maintenance and shutdown support, planned and reactive.",
                f"Enquiries: enquiries@{ctx.domain}",
            ],
        ),
        "price-list-2026-q3.pdf": pdf(
            "Trade Price List \u2014 Q3 2026",
            [
                "Mild steel plate, per tonne, cut and drilled.",
                "Handrail, standard section, per metre, primed.",
                "Walkway grating, per square metre, galvanised.",
                "Site erection, per day, two-man team plus plant.",
                "Prices exclude delivery and VAT. Valid to 30 September 2026.",
            ],
        ),
        "iso9001-certificate.pdf": pdf(
            "Certificate of Registration \u2014 ISO 9001:2015",
            [
                f"{ctx.company_legal}",
                f"Registered address: Wincolmlee Works, {ctx.city}",
                f"Certificate number: FS {ctx.number('cert/iso', 100000, 999999)}",
                "Scope: fabrication and erection of structural steelwork.",
                "Valid to 14 March 2027.",
            ],
        ),
        "tolerances-bs-en-1090-2.pdf": pdf(
            "Fabrication Tolerances \u2014 note for site staff",
            [
                "Essential tolerances are as tabled in BS EN 1090-2 Annex B.",
                "Functional tolerances class 1 unless the drawing says otherwise.",
                "Anything outside tolerance is reported before it leaves the works.",
                f"Queries: {ctx.person(3).name}, {ctx.person(3).email}",
            ],
        ),
        "delivery-and-access-notes.pdf": pdf(
            "Delivery and Site Access",
            [
                "Deliveries between 07:00 and 16:00, Monday to Friday.",
                "Articulated access to the north gate only.",
                "Hard hat, boots and hi-vis on the yard at all times.",
                f"Gatehouse: 01482 {ctx.number('tel/gate', 100000, 999999)}",
            ],
        ),
    }
