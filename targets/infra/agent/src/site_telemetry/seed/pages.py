"""Static page bodies for the three virtual hosts the deployment serves.

Everything here is written out once, at deployment time, and then served
untouched by Apache: there is no application server behind these three
hosts, so whatever a visitor reads is exactly what this module produced.
That is why every string that looks installation-specific -- the street
address, the phone number, the staff names, the job references on the
projects page, the certificate numbers -- is pulled from the `SeedContext`
rather than typed in literally: two installations of the same estate must
not read like carbon copies of each other, and nothing here may depend on
the clock or on randomness, because a redeploy with the same seed has to
produce byte-identical files.

The three hosts play different roles and are written accordingly:

* `www` is the public marketing site: home, about, services, capabilities,
  projects, careers, news, contact, the legal pages, and a small
  recruitment micro-site under `careers/portal/` that reads as if an
  outside agency built it -- different styling, its own footer, a credit
  line -- because that is how these things are usually commissioned.
* `static` carries the shared stylesheet, scripts and images used by the
  other two hosts (referenced from them by absolute URL) plus a couple of
  small third-party library files the company keeps a local copy of
  rather than pulling from the public internet. Apache is configured to
  refuse directory listings on this host, which is the whole reason it
  exists as a separate vhost instead of a folder under `www`.
* `docs` is the internal documentation host: the staff handbook and a
  placeholder for the operator API reference, which is not published to
  anonymous visitors.
"""

from __future__ import annotations

import struct
from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:
    from .context import SeedContext

__all__ = ["build_www", "build_static", "build_docs", "security_txt"]

# --------------------------------------------------------------------------
# Content pools. These are not seed-derived themselves; the seed decides
# which entries get picked and in what combination, via ctx.pick/ctx.number.
# --------------------------------------------------------------------------

_STREETS = (
    "Wincolmlee", "Cleveland Street", "Hedon Road", "Clarence Street",
    "Air Street", "Neptune Street", "English Street", "Stoneferry Road",
)
_ESTATES = (
    "Sutton Fields Industrial Estate", "Priory Park", "Salthouse Road Estate",
    "Wiltshire Road Industrial Estate", "Sculcoates Trading Estate",
)
_POSTCODE_AREAS = ("HU3", "HU7", "HU8", "HU9")
_PRIMARY_SHADES = ("#1c2b36", "#20323d", "#233a44", "#1a2a33", "#22343e")
_ACCENT = "#c1450e"

_SECTORS = (
    "a cold-store operator", "a chemical processing site", "a rail depot",
    "a food-packing plant", "a marine repair yard", "a grain terminal",
)
_PROJECT_SCOPES = (
    "a replacement handrail run along the loading bay",
    "a new access platform and stair tower serving three tank levels",
    "structural steelwork for a single-storey process extension",
    "re-coating of the existing frame after a condition survey",
    "a mezzanine floor and its supporting columns",
)

_HANDBOOK_PAGES = (
    ("health-and-safety.html", "Health and safety",
     "site rules, PPE requirements and the accident reporting line"),
    ("site-induction.html", "Site induction",
     "what a visitor or new starter is taken through before first entry"),
    ("quality-procedures.html", "Quality procedures",
     "the document control and inspection records behind the ISO 9001 file"),
)


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


# --------------------------------------------------------------------------
# Small binary assets built by hand, without any imaging library.
# --------------------------------------------------------------------------

def _favicon_bytes(primary_hex: str, accent_hex: str) -> bytes:
    """A minimal 16x16, 32bpp BMP-in-ICO: two-tone flange, no alpha holes."""
    primary = _hex_to_rgb(primary_hex)
    accent = _hex_to_rgb(accent_hex)
    size = 16
    pixel_data = bytearray()
    # BMP pixel rows are stored bottom-up.
    for y in range(size - 1, -1, -1):
        for _x in range(size):
            colour = accent if 6 <= y <= 9 else primary
            r, g, b = colour
            pixel_data += bytes((b, g, r, 255))
    and_mask_row = ((size + 31) // 32) * 4
    and_mask = bytes(and_mask_row * size)
    dib_header = struct.pack(
        "<IiiHHIIiiII",
        40, size, size * 2, 1, 32, 0, len(pixel_data) + len(and_mask), 0, 0, 0, 0,
    )
    image_data = dib_header + bytes(pixel_data) + and_mask
    icondir = struct.pack("<HHH", 0, 1, 1)
    icondirentry = struct.pack(
        "<BBBBHHII", size, size, 0, 0, 1, 32, len(image_data), 6 + 16,
    )
    return icondir + icondirentry + image_data


def _logo_svg(company_short: str, primary: str, accent: str) -> str:
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 220 56" role="img"
     aria-label="{company_short} logo">
  <rect x="2" y="10" width="36" height="36" fill="{primary}"/>
  <rect x="10" y="18" width="20" height="6" fill="{accent}"/>
  <rect x="10" y="28" width="20" height="6" fill="{accent}"/>
  <text x="48" y="26" font-family="Arial, sans-serif" font-size="16"
        font-weight="700" fill="{primary}">{company_short}</text>
  <text x="48" y="42" font-family="Arial, sans-serif" font-size="10"
        letter-spacing="1" fill="{primary}">FABRICATION</text>
</svg>
"""


def _works_svg(primary: str, accent: str) -> str:
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 160" role="img"
     aria-label="Fabrication shop roofline">
  <rect width="400" height="160" fill="#dfe3e6"/>
  <rect x="20" y="60" width="360" height="100" fill="{primary}"/>
  <polygon points="20,60 100,20 180,60" fill="{primary}"/>
  <polygon points="180,60 260,20 340,60" fill="{primary}"/>
  <rect x="60" y="90" width="24" height="70" fill="#dfe3e6"/>
  <rect x="150" y="90" width="24" height="70" fill="#dfe3e6"/>
  <rect x="240" y="90" width="24" height="70" fill="#dfe3e6"/>
  <rect x="320" y="90" width="24" height="70" fill="#dfe3e6"/>
  <rect x="30" y="8" width="8" height="60" fill="{accent}"/>
  <rect x="2" y="4" width="60" height="8" fill="{accent}"/>
</svg>
"""


def _marque_svg(initials: str, primary: str, accent: str) -> str:
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" role="img"
     aria-label="{initials} monogram">
  <circle cx="32" cy="32" r="30" fill="{primary}"/>
  <circle cx="32" cy="32" r="29" fill="none" stroke="{accent}" stroke-width="3"/>
  <text x="32" y="41" font-family="Arial, sans-serif" font-size="24"
        font-weight="700" fill="#ffffff" text-anchor="middle">{initials}</text>
</svg>
"""


def _static_svg(label: str, primary: str, accent: str) -> str:
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48" role="img"
     aria-label="{label}">
  <rect x="4" y="4" width="40" height="40" rx="4" fill="{primary}"/>
  <circle cx="24" cy="24" r="12" fill="none" stroke="{accent}" stroke-width="3"/>
</svg>
"""


# --------------------------------------------------------------------------
# Text assets: stylesheets and scripts.
# --------------------------------------------------------------------------

def _site_css(primary: str) -> str:
    return f"""/* Primary stylesheet for the public site. Layered on top of the
   shared reset served from the assets host, so this file only carries
   the things that differ between hosts: colours, spacing, nav layout. */
:root {{
  --primary: {primary};
  --accent: {_ACCENT};
  --text: #1c1c1c;
  --muted: #5a6570;
  --border: #d7dbdd;
}}
body {{
  font-family: Arial, Helvetica, sans-serif;
  color: var(--text);
  margin: 0;
  line-height: 1.5;
}}
header.site-header {{
  border-bottom: 3px solid var(--primary);
  padding: 12px 24px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
}}
header.site-header img.logo {{ height: 40px; }}
nav.site-nav ul {{
  list-style: none;
  display: flex;
  gap: 18px;
  margin: 0;
  padding: 0;
  flex-wrap: wrap;
}}
nav.site-nav a {{
  color: var(--primary);
  text-decoration: none;
  font-weight: 600;
}}
nav.site-nav a[aria-current="page"] {{ color: var(--accent); }}
main {{ max-width: 960px; margin: 0 auto; padding: 24px; }}
h1, h2, h3 {{ color: var(--primary); }}
.hero {{ display: grid; gap: 16px; margin-bottom: 24px; }}
.hero img {{ width: 100%; height: auto; border: 1px solid var(--border); }}
.card {{
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 16px;
  margin-bottom: 16px;
}}
.grid-two {{ display: grid; gap: 16px; grid-template-columns: 1fr 1fr; }}
@media (max-width: 640px) {{
  .grid-two {{ grid-template-columns: 1fr; }}
}}
table {{ border-collapse: collapse; width: 100%; }}
table caption {{ text-align: left; font-weight: 600; margin-bottom: 6px; }}
th, td {{
  border: 1px solid var(--border);
  padding: 8px 10px;
  text-align: left;
  vertical-align: top;
}}
form.contact-form label {{ display: block; margin-top: 12px; font-weight: 600; }}
form.contact-form input, form.contact-form textarea {{
  width: 100%;
  box-sizing: border-box;
  padding: 8px;
  border: 1px solid var(--border);
  font: inherit;
}}
form.contact-form button {{
  margin-top: 16px;
  background: var(--primary);
  color: #fff;
  border: none;
  padding: 10px 20px;
  font-weight: 600;
  cursor: pointer;
}}
#cookie-notice {{
  position: fixed;
  left: 0;
  right: 0;
  bottom: 0;
  background: var(--primary);
  color: #fff;
  padding: 14px 20px;
  display: flex;
  gap: 16px;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
}}
#cookie-notice a {{ color: #fff; }}
#cookie-notice button {{
  background: var(--accent);
  color: #fff;
  border: none;
  padding: 8px 16px;
  cursor: pointer;
  font-weight: 600;
}}
#cookie-notice[hidden] {{ display: none; }}
footer.site-footer {{
  border-top: 3px solid var(--primary);
  margin-top: 40px;
  padding: 20px 24px 60px;
  color: var(--muted);
  font-size: 0.9em;
}}
footer.site-footer a {{ color: var(--muted); }}
address {{ font-style: normal; }}
"""


def _site_js() -> str:
    return """/* Small behaviours shared by every page on this host: the nav
   collapses on narrow screens, and the cookie notice remembers a
   visitor's choice in localStorage rather than setting a cookie before
   consent has actually been given. */
(function () {
  "use strict";

  function setUpNav() {
    var toggle = document.querySelector("[data-nav-toggle]");
    var nav = document.querySelector(".site-nav");
    if (!toggle || !nav) {
      return;
    }
    toggle.addEventListener("click", function () {
      var open = nav.getAttribute("data-open") === "true";
      nav.setAttribute("data-open", open ? "false" : "true");
      toggle.setAttribute("aria-expanded", open ? "false" : "true");
    });
  }

  function setUpCookieNotice() {
    var notice = document.getElementById("cookie-notice");
    if (!notice) {
      return;
    }
    var stored = window.localStorage.getItem("cookie-choice");
    if (stored) {
      notice.setAttribute("hidden", "hidden");
      return;
    }
    var accept = notice.querySelector("[data-cookie-accept]");
    if (accept) {
      accept.addEventListener("click", function () {
        window.localStorage.setItem("cookie-choice", "accepted");
        notice.setAttribute("hidden", "hidden");
      });
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    setUpNav();
    setUpCookieNotice();
  });
})();
"""


def _insight_js() -> str:
    return """/* In-house page-view counter. It posts to our own collection path,
   never to a third-party address, and it must never be able to break
   the page it runs on -- hence the empty catch. */
(function () {
  "use strict";

  function send() {
    var payload = {
      path: window.location.pathname,
      referrer: document.referrer || "",
      width: window.innerWidth,
      height: window.innerHeight
    };
    try {
      var body = JSON.stringify(payload);
      if (navigator.sendBeacon) {
        navigator.sendBeacon("/assets/collect", body);
      } else {
        var pixel = new Image();
        pixel.src = "/assets/collect?d=" + encodeURIComponent(body);
      }
    } catch (err) {
      /* collection is best-effort only */
    }
  }

  if (document.readyState === "complete") {
    send();
  } else {
    window.addEventListener("load", send);
  }
})();
"""


def _common_css() -> str:
    return """/* Shared reset and typography, served once from the assets host and
   pulled in by the other two hosts as a first tag in <head>, before
   their own stylesheet. Nothing host-specific belongs in this file. */
*, *::before, *::after { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }
body { margin: 0; }
img, svg { max-width: 100%; display: block; }
a { text-decoration-skip-ink: auto; }
ul, ol { padding-left: 1.2em; }
"""


def _static_index_html(ctx: "SeedContext") -> str:
    return f"""<!doctype html>
<html lang="en-GB">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Assets -- {ctx.company}</title>
<meta name="description" content="Shared stylesheet, scripts and images
used by the {ctx.company_short} sites.">
<link rel="stylesheet" href="assets/css/common.css">
</head>
<body>
<main style="max-width:640px;margin:40px auto;font-family:Arial,sans-serif;">
<h1>Assets host</h1>
<p>This host carries the shared stylesheet, scripts and images used by
<a href="http://{ctx.www_host}/">{ctx.www_host}</a> and
<a href="http://{ctx.docs_host}/">{ctx.docs_host}</a>, plus local copies of
the two small third-party libraries the sites depend on, so that nothing
is fetched from outside our own domain. It does not carry any pages of
its own beyond this one.</p>
<p>Directory listing is switched off throughout this host.</p>
</main>
</body>
</html>
"""


def _reset_kit_css() -> str:
    return """/* reset-kit 1.4 -- vendored copy, kept local so the build never
   depends on the public internet being reachable. */
html, body, div, span, h1, h2, h3, h4, h5, h6, p, blockquote, table,
tr, th, td, form, fieldset, ul, ol, li {
  margin: 0;
  padding: 0;
  border: 0;
  font: inherit;
  vertical-align: baseline;
}
table { border-collapse: collapse; border-spacing: 0; }
"""


def _domready_js() -> str:
    return """/* domready 0.9 -- vendored copy. Runs the given function once the
   document is interactive, falling back to the load event on the
   handful of old engines that never fire DOMContentLoaded reliably. */
(function (root, factory) {
  root.domready = factory();
})(this, function () {
  return function (fn) {
    if (document.readyState !== "loading") {
      fn();
    } else {
      document.addEventListener("DOMContentLoaded", fn);
      window.addEventListener("load", fn);
    }
  };
});
"""


def _form_validate_js() -> str:
    return """/* form-validate 0.3 -- shared client-side check used by the forms
   on the main site and by the recruitment micro-site's application
   form, so the two do not each carry their own copy of the same
   handful of rules. */
(function () {
  "use strict";
  function attach(form) {
    form.addEventListener("submit", function (event) {
      var required = form.querySelectorAll("[required]");
      for (var i = 0; i < required.length; i += 1) {
        if (!required[i].value.trim()) {
          event.preventDefault();
          required[i].focus();
          return;
        }
      }
    });
  }
  document.addEventListener("DOMContentLoaded", function () {
    var forms = document.querySelectorAll("form[data-validate]");
    for (var i = 0; i < forms.length; i += 1) {
      attach(forms[i]);
    }
  });
})();
"""


def _nav_toggle_js() -> str:
    return """/* nav-toggle 0.2 -- generic show/hide helper for a element pair with
   matching data-toggle / data-toggle-for attributes. Older utility kept
   around for pages that predate the current site.js. */
(function () {
  "use strict";
  document.addEventListener("click", function (event) {
    var button = event.target.closest("[data-toggle]");
    if (!button) {
      return;
    }
    var id = button.getAttribute("data-toggle");
    var target = document.getElementById(id);
    if (target) {
      target.hidden = !target.hidden;
    }
  });
})();
"""


# --------------------------------------------------------------------------
# HTML page shell shared by the www host.
# --------------------------------------------------------------------------

_WWW_NAV = (
    ("index.html", "Home"),
    ("about.html", "About"),
    ("services.html", "Services"),
    ("capabilities.html", "Capabilities"),
    ("projects.html", "Projects"),
    ("careers.html", "Careers"),
    ("news/index.html", "News"),
    ("contact.html", "Contact"),
)


def _address_block(ctx: "SeedContext") -> tuple[str, str, str]:
    street = ctx.pick("address/street", _STREETS)
    estate = ctx.pick("address/estate", _ESTATES)
    unit = ctx.number("address/unit", 1, 24)
    area = ctx.pick("address/postcode-area", _POSTCODE_AREAS)
    district = ctx.number("address/postcode-district", 1, 9)
    letters = "".join(
        chr(ord("A") + ctx.number(f"address/postcode-letter-{i}", 0, 25))
        for i in range(2)
    )
    postcode = f"{area} {district}{letters}"
    address = (
        f"Unit {unit}, {estate}, {street}, {ctx.city} {postcode}"
    )
    phone_number = ctx.number("phone", 200000, 999999)
    phone = f"01482 {str(phone_number)[:3]} {str(phone_number)[3:]}"
    return address, phone, postcode


def _www_shell(
    ctx: "SeedContext",
    title: str,
    description: str,
    active: str,
    body: str,
    prefix: str = "",
) -> bytes:
    address, phone, _postcode = _address_block(ctx)
    nav_items = []
    for href, label in _WWW_NAV:
        current = ' aria-current="page"' if href == active else ""
        nav_items.append(f'<li><a href="{prefix}{href}"{current}>{label}</a></li>')
    nav_html = "\n      ".join(nav_items)
    html = f"""<!doctype html>
<html lang="en-GB">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} -- {ctx.company}</title>
<meta name="description" content="{description}">
<link rel="icon" href="{prefix}favicon.ico">
<link rel="stylesheet" href="http://{ctx.static_host}/assets/css/common.css">
<link rel="stylesheet" href="{prefix}assets/css/site.css">
</head>
<body>
<header class="site-header">
  <a href="{prefix}index.html">
    <img class="logo" src="{prefix}assets/img/logo.svg" alt="{ctx.company} logo">
  </a>
  <button type="button" data-nav-toggle aria-expanded="false" aria-controls="main-nav">
    Menu
  </button>
  <nav class="site-nav" id="main-nav" aria-label="Primary">
    <ul>
      {nav_html}
    </ul>
  </nav>
</header>
<main>
{body}
</main>
<footer class="site-footer">
  <div class="grid-two">
    <address>
      {ctx.company_legal}<br>
      {address}<br>
      Tel: <a href="tel:+44{phone.replace(" ", "")[1:]}">{phone}</a><br>
      <a href="mailto:info@{ctx.domain}">info@{ctx.domain}</a>
    </address>
    <ul>
      <li><a href="{prefix}legal/privacy.html">Privacy notice</a></li>
      <li><a href="{prefix}legal/terms.html">Terms of use</a></li>
      <li><a href="{prefix}legal/cookies.html">Cookies</a></li>
      <li><a href="http://{ctx.docs_host}/">Staff handbook</a></li>
    </ul>
  </div>
  <p>&copy; {ctx.year} {ctx.company_legal}. All rights reserved.</p>
</footer>
<div id="cookie-notice" role="region" aria-label="Cookie notice">
  <p>This site uses a small number of first-party cookies to remember your
  preferences. See our <a href="{prefix}legal/cookies.html">cookies page</a>
  for details.</p>
  <button type="button" data-cookie-accept>Accept</button>
</div>
<script src="{prefix}assets/js/site.js"></script>
<script src="{prefix}assets/js/insight.js"></script>
</body>
</html>
"""
    return html.encode("utf-8")


# --------------------------------------------------------------------------
# www: page bodies
# --------------------------------------------------------------------------

def _index_body(ctx: "SeedContext") -> str:
    md = ctx.person(0)
    return f"""<section class="hero">
  <img src="assets/img/works.svg" alt="{ctx.company} fabrication shop">
  <h1>Structural steel fabrication and site services in {ctx.city}</h1>
  <p>{ctx.company_legal} fabricates and erects structural steelwork,
  handrails and access platforms for industrial and commercial sites
  across the Humber estuary. Most of what leaves the shop goes up within
  fifteen miles of it.</p>
</section>
<section class="grid-two">
  <div class="card">
    <h2>What we do</h2>
    <p>Fabrication, site erection, handrails and access platforms,
    protective coatings, and ongoing maintenance contracts for the
    plant we have already put up. See the <a href="services.html">full
    list of services</a>.</p>
  </div>
  <div class="card">
    <h2>Who we are</h2>
    <p>A works team of welders and fitters, a small drawing office, and
    a site crew that does its own erection rather than sub-contracting
    it out. {md.name}, {md.role}, runs the business day to day.</p>
  </div>
</section>
<section class="card">
  <h2>Accreditations</h2>
  <p>ISO 9001:2015 quality management, CE marking to BS EN 1090-2, and
  CHAS accreditation for the site work. Certificate numbers are on the
  <a href="about.html">about page</a>.</p>
</section>
"""


def _about_body(ctx: "SeedContext") -> str:
    md = ctx.person(0)
    works = ctx.person(1)
    quality = ctx.person(3)
    contracts = ctx.person(5)
    founded = ctx.year - ctx.number("founded/years-ago", 14, 31)
    iso_cert = ctx.hexname("iso9001/cert", 8).upper()
    chas_cert = ctx.hexname("chas/cert", 8).upper()
    exc_class = ctx.pick("exc/class", ("EXC2", "EXC3"))
    return f"""<h1>About {ctx.company}</h1>
<p>{ctx.company_legal} was set up in {founded} to take on the handrail
and stair-tower work that the bigger yards on the estuary did not want
in small lots. The order book is still mostly small and medium
contracts: a few tonnes here, a platform replacement there, occasionally
a full building frame.</p>
<h2>Management</h2>
<table>
  <caption>Who to speak to</caption>
  <tr><th>Name</th><th>Role</th><th>Contact</th></tr>
  <tr><td>{md.name}</td><td>{md.role}</td>
      <td><a href="mailto:{md.email}">{md.email}</a></td></tr>
  <tr><td>{works.name}</td><td>{works.role}</td>
      <td><a href="mailto:{works.email}">{works.email}</a></td></tr>
  <tr><td>{quality.name}</td><td>{quality.role}</td>
      <td><a href="mailto:{quality.email}">{quality.email}</a></td></tr>
  <tr><td>{contracts.name}</td><td>{contracts.role}</td>
      <td><a href="mailto:{contracts.email}">{contracts.email}</a></td></tr>
</table>
<h2>Accreditations</h2>
<ul>
  <li>ISO 9001:2015 quality management -- certificate {iso_cert}</li>
  <li>CE marking to BS EN 1090-2, execution class {exc_class}</li>
  <li>CHAS accreditation -- reference {chas_cert}</li>
</ul>
"""


def _services_body(ctx: "SeedContext") -> str:
    return """<h1>Services</h1>
<div class="card">
  <h2>Structural steel fabrication</h2>
  <p>Cutting, drilling and welding of beams, columns and connections to
  CE-marked drawings, from a single bracket to a full building frame.
  Everything that leaves the shop is traceable back to its cutting
  list and its welder.</p>
</div>
<div class="card">
  <h2>Site erection</h2>
  <p>We erect what we fabricate. A crew of our own fitters, working to
  the method statement agreed at order stage, rather than a
  sub-contracted gang meeting the steel for the first time on site.</p>
</div>
<div class="card">
  <h2>Handrails and access platforms</h2>
  <p>Galvanised or painted handrail, ladders, stair towers and access
  platforms to suit an existing structure. This is the work the
  business was originally built around, and it is still most of what
  goes out of the yard in a given month.</p>
</div>
<div class="card">
  <h2>Protective coatings</h2>
  <p>Shot-blast to Sa2.5 and a two- or three-coat paint system applied
  in-house before delivery, or a re-coat of existing steelwork after a
  condition survey.</p>
</div>
<div class="card">
  <h2>Maintenance contracts</h2>
  <p>Scheduled inspection and repair of steelwork we, or someone else,
  put up years ago: loose handrail, corroded fixings, coating
  breakdown. Most maintenance customers started as a one-off project.</p>
</div>
"""


def _capabilities_body(ctx: "SeedContext") -> str:
    bay_length = ctx.number("shop/bay-length", 28, 46)
    crane_capacity = ctx.number("shop/crane-tonnes", 5, 15)
    welders = ctx.number("shop/welders", 6, 14)
    storage = ctx.number("shop/storage-tonnes", 80, 260)
    return f"""<h1>Capabilities</h1>
<p>The fabrication shop is a single bay {bay_length} metres long, served
by an overhead crane rated at {crane_capacity} tonnes. Flatbed and
crane-offload wagons deliver most weeks; there is standing steel stock
for roughly {storage} tonnes of work at any one time.</p>
<h2>Welding</h2>
<p>MMA, MIG/MAG and, for the stainless handrail work, TIG. Welders are
coded to BS EN ISO 9606 for the processes and positions they carry out;
records are kept against each certificate and renewed before expiry.</p>
<h2>Coatings line</h2>
<p>Shot-blast cabinet, wet-paint booth, and a drying bay big enough for
a stair-tower section in one piece rather than in cut lengths.</p>
<h2>Workforce</h2>
<p>{welders} coded welders and fitters in the shop, plus the site
erection crew and the drawing office. Numbers move a little with the
order book, as they do at most fabricators this size.</p>
"""


def _projects_body(ctx: "SeedContext") -> str:
    rows = []
    for i in range(4):
        job_ref = f"NF-{ctx.number(f'project/{i}/ref', 1000, 9999)}"
        sector = ctx.pick(f"project/{i}/sector", _SECTORS)
        scope = ctx.pick(f"project/{i}/scope", _PROJECT_SCOPES)
        tonnage = ctx.number(f"project/{i}/tonnage", 2, 40)
        year = ctx.year - ctx.number(f"project/{i}/years-ago", 0, 3)
        estate = ctx.pick(f"project/{i}/estate", _ESTATES)
        rows.append(
            f"<tr><td>{job_ref}</td><td>{year}</td>"
            f"<td>{sector.capitalize()} on the {estate}</td>"
            f"<td>{scope}, {tonnage} tonnes</td></tr>"
        )
    rows_html = "\n  ".join(rows)
    return f"""<h1>Recent projects</h1>
<p>Client names are left off this list by request, which is normal for
work on live industrial sites; the job reference and scope are real.</p>
<table>
  <caption>Selected work, most recent first</caption>
  <tr><th>Job ref</th><th>Year</th><th>Site</th><th>Scope</th></tr>
  {rows_html}
</table>
"""


def _careers_body(ctx: "SeedContext") -> str:
    estimator = ctx.person(2)
    return f"""<h1>Careers</h1>
<p>We take on coded welders, fitters and, less often, an apprentice
straight from the local college. Vacancies are usually filled from
word of mouth before they are advertised anywhere else.</p>
<h2>Current vacancies</h2>
<ul>
  <li>Coded welder (MIG/MAG) -- shop-based, full time</li>
  <li>Site erector -- some travel within East Yorkshire</li>
  <li>Trainee estimator -- reporting to {estimator.name}</li>
</ul>
<p>Applications go through our recruitment micro-site, which an outside
agency built and hosts the current vacancy listing and application
form: <a href="careers/portal/index.html">open the careers portal</a>.</p>
"""


def _contact_body(ctx: "SeedContext") -> str:
    address, phone, _postcode = _address_block(ctx)
    estimator = ctx.person(2)
    form_token = ctx.token("contact/form-token", 12)
    return f"""<h1>Contact</h1>
<div class="grid-two">
  <div>
    <h2>Yard and office</h2>
    <address>
      {ctx.company_legal}<br>
      {address}<br>
      Tel: {phone}<br>
      <a href="mailto:info@{ctx.domain}">info@{ctx.domain}</a>
    </address>
    <p>Opening hours: Monday to Friday, 07:30 to 16:30. The yard is
    closed at weekends and on public holidays.</p>
    <p>For a quote, contact {estimator.name} directly at
    <a href="mailto:{estimator.email}">{estimator.email}</a>.</p>
  </div>
  <div>
    <h2>Send a message</h2>
    <form class="contact-form" data-validate
          action="https://forms.{ctx.domain}/f/{form_token}" method="post">
      <label for="name">Name</label>
      <input id="name" name="name" type="text" required>
      <label for="email">Email</label>
      <input id="email" name="email" type="email" required>
      <label for="message">Message</label>
      <textarea id="message" name="message" rows="5" required></textarea>
      <button type="submit">Send</button>
    </form>
    <p><small>Messages are handled by our form-processing subdomain,
    which sits on our own domain, so nothing here is sent off-site.
    </small></p>
  </div>
</div>
"""


def _news_index_body(ctx: "SeedContext", articles: Sequence[tuple[str, str, str]]) -> str:
    items = "\n  ".join(
        f'<li><a href="{path}">{title}</a> -- {date}</li>'
        for path, title, date in articles
    )
    return f"""<h1>News</h1>
<ul>
  {items}
</ul>
"""


def _news_article_body(title: str, dateline: str, paragraphs: Sequence[str]) -> str:
    body = "\n".join(f"<p>{p}</p>" for p in paragraphs)
    return f"""<article>
  <h1>{title}</h1>
  <p><em>{dateline}</em></p>
  {body}
</article>
"""


def _legal_privacy_body(ctx: "SeedContext") -> str:
    ico_ref = ctx.hexname("legal/ico-ref", 8).upper()
    companies_house = ctx.number("legal/companies-house", 1000000, 9999999)
    return f"""<h1>Privacy notice</h1>
<p>{ctx.company_legal} (company number {companies_house}) is the data
controller for information submitted through this site. Our
registration with the Information Commissioner's Office is {ico_ref}.</p>
<h2>What we collect</h2>
<p>Contact form submissions carry a name, an email address and a
message. Site visits are recorded in aggregate through our own
first-party page-view counter, described in the
<a href="cookies.html">cookies notice</a>; no identifying data is
shared with anyone outside the business.</p>
<h2>How long we keep it</h2>
<p>Enquiry records are kept for six years to satisfy our own contract
and accounting records, then deleted.</p>
<h2>Your rights</h2>
<p>You can ask to see, correct or have removed any information we hold
about you by writing to <a href="mailto:info@{ctx.domain}">
info@{ctx.domain}</a>.</p>
"""


def _legal_terms_body(ctx: "SeedContext") -> str:
    return f"""<h1>Terms of use</h1>
<p>This site describes the services offered by {ctx.company_legal} and
is provided for general information. Quotations issued separately, in
writing, take precedence over anything published here.</p>
<p>Content on this site may not be reproduced without permission.
Project references are given in general terms; site names and client
identities are withheld unless a customer has agreed otherwise.</p>
<p>Governing law: England and Wales.</p>
"""


def _legal_cookies_body(ctx: "SeedContext") -> str:
    return f"""<h1>Cookies</h1>
<p>This site sets one cookie-equivalent value, stored in your browser's
local storage rather than as a cookie, to remember that you have seen
the notice on this page.</p>
<h2>Page-view counter</h2>
<p>A small first-party script, served from this site as
<code>/assets/js/insight.js</code>, records the page you are on and the
size of your browser window against our own collection address. It
does not use cookies and it does not send anything to a third party.
</p>
<p>No advertising or analytics service outside {ctx.domain} is used
anywhere on this site.</p>
"""


def _security_txt(ctx: "SeedContext") -> str:
    expiry = ctx.year + 1
    return f"""Contact: mailto:security@{ctx.domain}
Expires: {expiry}-01-01T00:00:00.000Z
Preferred-Languages: en
Canonical: http://{ctx.www_host}/.well-known/security.txt
"""


def _robots_txt(ctx: "SeedContext") -> str:
    return f"""User-agent: *
Disallow: /api-docs/
Disallow: /media/
Disallow: /careers/portal/
Sitemap: http://{ctx.www_host}/sitemap.xml
"""


def _sitemap_xml(ctx: "SeedContext", paths: Sequence[str]) -> str:
    urls = "\n  ".join(
        f"<url><loc>http://{ctx.www_host}/{p}</loc></url>" for p in paths
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  {urls}
</urlset>
"""


# --------------------------------------------------------------------------
# careers/portal: a small, differently-styled micro-site.
# --------------------------------------------------------------------------

def _portal_css() -> str:
    return """/* Styling for the recruitment micro-site. Kept separate from the
   main site.css on purpose: this section was commissioned from an
   outside agency and never folded back into the main design. */
body {
  font-family: "Trebuchet MS", Verdana, sans-serif;
  margin: 0;
  background: #f4f2ee;
  color: #2a2a2a;
}
header.portal-header {
  background: #3c2a5e;
  color: #fff;
  padding: 20px;
}
main.portal-main { max-width: 760px; margin: 0 auto; padding: 24px; }
.vacancy {
  background: #fff;
  border-left: 4px solid #3c2a5e;
  padding: 14px 18px;
  margin-bottom: 14px;
}
form.apply-form label { display: block; margin-top: 10px; font-weight: 600; }
form.apply-form input, form.apply-form textarea {
  width: 100%;
  box-sizing: border-box;
  padding: 8px;
  border: 1px solid #ccc;
  font: inherit;
}
form.apply-form button {
  margin-top: 14px;
  background: #3c2a5e;
  color: #fff;
  border: none;
  padding: 10px 20px;
  cursor: pointer;
}
footer.portal-footer {
  padding: 16px 24px 40px;
  color: #6a6a6a;
  font-size: 0.85em;
}
"""


def _portal_shell(ctx: "SeedContext", title: str, description: str, body: str) -> bytes:
    html = f"""<!doctype html>
<html lang="en-GB">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} -- {ctx.company} careers</title>
<meta name="description" content="{description}">
<link rel="stylesheet" href="http://{ctx.static_host}/assets/css/common.css">
<link rel="stylesheet" href="http://{ctx.static_host}/vendor/reset-kit/reset.min.css">
<link rel="stylesheet" href="assets/portal.css">
</head>
<body>
<header class="portal-header">
  <p>{ctx.company} careers</p>
  <h1>{title}</h1>
</header>
<main class="portal-main">
{body}
</main>
<footer class="portal-footer">
  <p><a href="../../index.html">Back to {ctx.company_short}.com</a></p>
  <p>Careers portal built and hosted for {ctx.company_short} by an
  outside recruitment agency.</p>
</footer>
<script src="http://{ctx.static_host}/vendor/domready/domready.min.js"></script>
<script src="http://{ctx.static_host}/assets/js/form-validate.js"></script>
</body>
</html>
"""
    return html.encode("utf-8")


def _portal_index_body(ctx: "SeedContext") -> str:
    return """<div class="vacancy">
  <h2>Coded welder (MIG/MAG)</h2>
  <p>Shop-based, full time. Current welding coding required.</p>
</div>
<div class="vacancy">
  <h2>Site erector</h2>
  <p>East Yorkshire sites, van and tools provided.</p>
</div>
<div class="vacancy">
  <h2>Trainee estimator</h2>
  <p>Office-based, day release for study supported.</p>
</div>
<p><a href="apply.html">Apply for one of these roles</a></p>
"""


def _portal_apply_body(ctx: "SeedContext") -> str:
    token = ctx.token("careers/form-token", 12)
    return f"""<form class="apply-form" data-validate
      action="https://forms.{ctx.domain}/f/{token}" method="post">
  <label for="role">Role applied for</label>
  <input id="role" name="role" type="text" required>
  <label for="name">Full name</label>
  <input id="name" name="name" type="text" required>
  <label for="email">Email</label>
  <input id="email" name="email" type="email" required>
  <label for="notes">Relevant experience</label>
  <textarea id="notes" name="notes" rows="5" required></textarea>
  <button type="submit">Submit application</button>
</form>
"""


# --------------------------------------------------------------------------
# docs host
# --------------------------------------------------------------------------

def _docs_shell(ctx: "SeedContext", title: str, description: str, body: str,
                 prefix: str = "") -> bytes:
    html = f"""<!doctype html>
<html lang="en-GB">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} -- {ctx.company} handbook</title>
<meta name="description" content="{description}">
<link rel="stylesheet" href="http://{ctx.static_host}/assets/css/common.css">
<style>
body {{ font-family: Georgia, "Times New Roman", serif; margin: 0 auto;
        max-width: 760px; padding: 24px; color: #202020; }}
header {{ border-bottom: 2px solid #20323d; margin-bottom: 20px; }}
footer {{ margin-top: 40px; color: #5a6570; font-size: 0.9em; }}
</style>
</head>
<body>
<header>
  <p><a href="{prefix}index.html">{ctx.company} handbook</a></p>
</header>
<main>
{body}
</main>
<footer>
  <p><a href="http://{ctx.www_host}/">{ctx.www_host}</a> --
     internal documentation, not for public distribution.</p>
</footer>
</body>
</html>
"""
    return html.encode("utf-8")


def _docs_index_body(ctx: "SeedContext") -> str:
    items = "\n  ".join(
        f'<li><a href="handbook/{path}">{title}</a> -- {blurb}</li>'
        for path, title, blurb in _HANDBOOK_PAGES
    )
    return f"""<h1>{ctx.company} staff handbook</h1>
<p>Working documents for staff and site visitors. Nothing here is
published to the public site.</p>
<ul>
  {items}
</ul>
<p>See also the <a href="api/index.html">operator API reference</a>.</p>
"""


def _handbook_page_body(ctx: "SeedContext", path: str, title: str) -> str:
    if path == "health-and-safety.html":
        supervisor = ctx.person(4)
        return f"""<h1>{title}</h1>
<p>Hard hat, safety boots and hi-vis are worn on the shop floor and on
site at all times, visitors included. Eye protection is required at
the cutting stations and the coatings booth.</p>
<p>Any accident, however small, is reported to {supervisor.name},
{supervisor.role}, before the end of the shift it happened on.</p>
"""
    if path == "site-induction.html":
        return f"""<h1>{title}</h1>
<p>Every visitor signs in at the office, is issued PPE if they do not
already have their own, and is walked through the fire assembly point
and the welfare facilities before going onto the shop floor.</p>
<p>Contractors working on a live site follow the induction set out in
that site's own method statement, not this one.</p>
"""
    quality = ctx.person(3)
    return f"""<h1>{title}</h1>
<p>Every job carries a cutting list, a weld map and a material
certificate trail, filed against its job reference. {quality.name},
{quality.role}, signs off the inspection record before dispatch.</p>
<p>These records are what an ISO 9001 audit is shown; keeping them
current is not optional paperwork, it is most of what the certificate
actually checks.</p>
"""


def _docs_api_index_body(ctx: "SeedContext") -> str:
    return """<h1>API reference</h1>
<p>The current API description is available to signed-in operators
only. There is nothing further to see here for anonymous visitors.</p>
"""


# --------------------------------------------------------------------------
# Public builders
# --------------------------------------------------------------------------

def build_www(ctx: "SeedContext") -> dict[str, bytes]:
    primary = ctx.pick("brand/primary", _PRIMARY_SHADES)
    initials = "".join(word[0] for word in ctx.company.split())
    out: dict[str, bytes] = {}

    out["index.html"] = _www_shell(
        ctx, "Home",
        f"{ctx.company_legal}: structural steel fabrication and site "
        f"services based in {ctx.city}.",
        "index.html", _index_body(ctx),
    )
    out["about.html"] = _www_shell(
        ctx, "About",
        f"Management, accreditations and history of {ctx.company_legal}.",
        "about.html", _about_body(ctx),
    )
    out["services.html"] = _www_shell(
        ctx, "Services",
        "Fabrication, site erection, handrails, coatings and maintenance.",
        "services.html", _services_body(ctx),
    )
    out["capabilities.html"] = _www_shell(
        ctx, "Capabilities",
        "Fabrication shop plant, welding coding and coatings line.",
        "capabilities.html", _capabilities_body(ctx),
    )
    out["projects.html"] = _www_shell(
        ctx, "Projects",
        "Recent structural steel and access platform projects.",
        "projects.html", _projects_body(ctx),
    )
    out["careers.html"] = _www_shell(
        ctx, "Careers",
        f"Current vacancies at {ctx.company_legal}.",
        "careers.html", _careers_body(ctx),
    )
    out["contact.html"] = _www_shell(
        ctx, "Contact",
        f"Address, phone number and enquiry form for {ctx.company_legal}.",
        "contact.html", _contact_body(ctx),
    )

    # News: three dated articles plus their index.
    months = ("March", "May", "July")
    days = (
        ctx.number("news/0/day", 1, 27),
        ctx.number("news/1/day", 1, 27),
        ctx.number("news/2/day", 1, 27),
    )
    supervisor = ctx.person(4)
    quality = ctx.person(3)
    articles_meta = [
        (
            f"news/{ctx.year}-03-{days[0]:02d}-access-platform-completed.html",
            "New access platform completed",
            f"{ctx.city}, {months[0]} {ctx.year}",
            [
                "A new access platform and stair tower, ordered after a "
                "routine inspection found the previous ladder past its "
                "service life, was handed over on schedule this month.",
                f"\"It's a straightforward job in most respects,\" said "
                f"{supervisor.name}, {supervisor.role}, \"but the tank "
                f"farm never stops running, so every lift had to be "
                f"planned around it.\"",
            ],
        ),
        (
            f"news/{ctx.year}-05-{days[1]:02d}-chas-reaccreditation.html",
            "CHAS reaccreditation confirmed",
            f"{ctx.city}, {months[1]} {ctx.year}",
            [
                f"{ctx.company_legal} has renewed its CHAS accreditation "
                f"for another year, following the annual review of our "
                f"health and safety documentation.",
                f"{quality.name}, {quality.role}, put together this "
                f"year's submission: \"most of the work is making sure "
                f"the paperwork matches what actually happens in the "
                f"yard, not the other way round.\"",
            ],
        ),
        (
            f"news/{ctx.year}-07-{days[2]:02d}-apprenticeship-intake.html",
            "New apprentice starting this autumn",
            f"{ctx.city}, {months[2]} {ctx.year}",
            [
                "We have taken on another apprentice welder for the "
                "autumn intake, continuing the day-release arrangement "
                "we have run with the local college for a number of "
                "years now.",
                "Most of our current coded welders came up through the "
                "same route, which is part of why we keep doing it.",
            ],
        ),
    ]
    news_list = []
    for path, title, dateline, paragraphs in articles_meta:
        rel = path[len("news/"):]
        news_list.append((rel, title, dateline))
        out[path] = _www_shell(
            ctx, title, f"{title} -- {ctx.company} news.",
            "news/index.html", _news_article_body(title, dateline, paragraphs),
            prefix="../",
        )
    out["news/index.html"] = _www_shell(
        ctx, "News", f"News from {ctx.company_legal}.",
        "news/index.html", _news_index_body(ctx, news_list), prefix="../",
    )

    out["legal/privacy.html"] = _www_shell(
        ctx, "Privacy notice", "How we handle personal information.",
        "legal/privacy.html", _legal_privacy_body(ctx), prefix="../",
    )
    out["legal/terms.html"] = _www_shell(
        ctx, "Terms of use", "Terms governing use of this site.",
        "legal/terms.html", _legal_terms_body(ctx), prefix="../",
    )
    out["legal/cookies.html"] = _www_shell(
        ctx, "Cookies", "What this site stores in your browser.",
        "legal/cookies.html", _legal_cookies_body(ctx), prefix="../",
    )

    out["assets/css/site.css"] = _site_css(primary).encode("utf-8")
    out["assets/js/site.js"] = _site_js().encode("utf-8")
    out["assets/js/insight.js"] = _insight_js().encode("utf-8")
    out["assets/img/logo.svg"] = _logo_svg(
        ctx.company_short, primary, _ACCENT).encode("utf-8")
    out["assets/img/works.svg"] = _works_svg(primary, _ACCENT).encode("utf-8")
    out["assets/img/marque.svg"] = _marque_svg(
        initials, primary, _ACCENT).encode("utf-8")
    out["favicon.ico"] = _favicon_bytes(primary, _ACCENT)

    public_paths = [
        "index.html", "about.html", "services.html", "capabilities.html",
        "projects.html", "careers.html", "contact.html", "news/index.html",
        "legal/privacy.html", "legal/terms.html", "legal/cookies.html",
    ] + [f"news/{rel}" for rel, _t, _d in news_list]

    out["robots.txt"] = _robots_txt(ctx).encode("utf-8")
    out["sitemap.xml"] = _sitemap_xml(ctx, public_paths).encode("utf-8")
    # security.txt content is generated by _security_txt() above, but it is
    # not placed in this mapping: every path here is a plain relative path
    # with no leading dot, and ".well-known/" starts with one. The deploy
    # step that assembles the document root is responsible for writing
    # dotted top-level paths such as .well-known/ once the rest of this
    # tree has been laid down.

    # Careers portal, styled and hosted as if by an outside agency.
    out["careers/portal/index.html"] = _portal_shell(
        ctx, "Vacancies", f"Current vacancies at {ctx.company_short}.",
        _portal_index_body(ctx),
    )
    out["careers/portal/apply.html"] = _portal_shell(
        ctx, "Apply", f"Application form for {ctx.company_short} vacancies.",
        _portal_apply_body(ctx),
    )
    out["careers/portal/assets/portal.css"] = _portal_css().encode("utf-8")

    return out


def build_static(ctx: "SeedContext") -> dict[str, bytes]:
    primary = ctx.pick("brand/primary", _PRIMARY_SHADES)
    out: dict[str, bytes] = {
        "index.html": _static_index_html(ctx).encode("utf-8"),
        "assets/css/common.css": _common_css().encode("utf-8"),
        "assets/js/nav-toggle.js": _nav_toggle_js().encode("utf-8"),
        "assets/js/form-validate.js": _form_validate_js().encode("utf-8"),
        "assets/img/pattern.svg": _static_svg(
            "background pattern", primary, _ACCENT).encode("utf-8"),
        "assets/img/placeholder.svg": _static_svg(
            "image placeholder", primary, _ACCENT).encode("utf-8"),
        "assets/img/spinner.svg": _static_svg(
            "loading indicator", primary, _ACCENT).encode("utf-8"),
        "vendor/reset-kit/reset.min.css": _reset_kit_css().encode("utf-8"),
        "vendor/domready/domready.min.js": _domready_js().encode("utf-8"),
    }
    return out


def build_docs(ctx: "SeedContext") -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    out["index.html"] = _docs_shell(
        ctx, "Handbook", f"{ctx.company} staff handbook.",
        _docs_index_body(ctx),
    )
    for path, title, _blurb in _HANDBOOK_PAGES:
        out[f"handbook/{path}"] = _docs_shell(
            ctx, title, f"{title} -- {ctx.company} handbook.",
            _handbook_page_body(ctx, path, title), prefix="../",
        )
    out["api/index.html"] = _docs_shell(
        ctx, "API reference", "Operator API reference.",
        _docs_api_index_body(ctx), prefix="../",
    )
    return out


def security_txt(ctx: "SeedContext") -> bytes:
    """Written separately from the page tree: it lives under a dotted directory, and
    the routine that assembles the document root is the one that handles those."""
    return _security_txt(ctx).encode("utf-8")
