/**
 * The document shell and the static assets.
 *
 * Everything a browser sees before JavaScript runs is here: one HTML file, the built
 * bundle, and the handful of files a crawler expects at fixed paths. There is no
 * server-side rendering — the client owns every page — so the shell is the same bytes
 * for every route apart from the per-response nonce.
 */
import { randomBytes } from "node:crypto";
import fs from "node:fs";
import path from "node:path";

import express from "express";

import config from "./config.js";
import { deriveIdentity } from "./lib/identity.js";
import { recordRender } from "./lib/navlog.js";

const identity = deriveIdentity(config.deploySeed);

const PAGE_ROUTES = [
  "/", "/catalog", "/catalog/:slug", "/product/:slug", "/search", "/stores", "/gift-cards",
  "/pages/:slug", "/support", "/support/tickets/:id", "/cart", "/checkout", "/sign-in",
  "/sign-up", "/account", "/account/orders", "/account/orders/:id", "/account/addresses",
  "/account/payment-methods", "/account/preferences", "/account/saved-searches",
  "/account/wishlist", "/account/wallet", "/account/loyalty", "/account/profile",
  "/admin", "/admin/orders", "/admin/coupons", "/admin/imports", "/admin/support",
];

/**
 * Report-only content security policy.
 *
 * The enforcement rollout is blocked on two inline scripts in the checkout iframe, so
 * the policy runs in report-only mode and the violations land in policy_reports. Our own
 * inline snippets carry the per-response nonce, so what remains in that table is
 * genuinely unexpected.
 */
function policyHeader(nonce) {
  return [
    "default-src 'self'",
    `script-src-elem 'self' 'nonce-${nonce}'`,
    "script-src-attr 'none'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: https:",
    "connect-src 'self'",
    "frame-ancestors 'self'",
    "base-uri 'self'",
    "form-action 'self'",
    "report-uri /api/policy-reports",
  ].join("; ");
}

function shellHtml(nonce, assets) {
  const title = `${identity.houseName} — everyday kit that lasts`;
  return `<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>${title}</title>
    <meta name="description" content="${identity.houseName}: hard-wearing outdoor and household goods, made to be repaired rather than replaced." />
    <link rel="icon" href="/favicon.ico" sizes="any" />
    <link rel="manifest" href="/manifest.webmanifest" />
    <link rel="canonical" href="${config.publicOrigin || ""}/" />
    <meta property="og:site_name" content="${identity.houseName}" />
${assets.css.map((href) => `    <link rel="stylesheet" href="${href}" />`).join("\n")}
    <script nonce="${nonce}">
      window.__STORE__ = { name: ${JSON.stringify(identity.houseName)}, currency: "EUR", locale: "en-GB" };
    </script>
    <script nonce="${nonce}" defer src="/assets/insight.js" data-site="${identity.domain}"></script>
  </head>
  <body>
    <div id="root"></div>
    <noscript>
      This shop needs JavaScript. Call us on +44 20 7946 0311 and we will take the order over the phone.
    </noscript>
${assets.js.map((src) => `    <script type="module" src="${src}"></script>`).join("\n")}
  </body>
</html>
`;
}

/** Read the built asset names once, so a request never touches the manifest. */
function readAssets(webRoot) {
  const fallback = { js: ["/assets/app.js"], css: ["/assets/app.css"] };
  try {
    const manifest = JSON.parse(
      fs.readFileSync(path.join(webRoot, ".vite", "manifest.json"), "utf8"),
    );
    const entry = Object.values(manifest).find((e) => e.isEntry);
    if (!entry) return fallback;
    return {
      js: [`/${entry.file}`],
      css: (entry.css ?? []).map((f) => `/${f}`),
    };
  } catch {
    return fallback;
  }
}

export function installShell(app, webRoot) {
  const assets = readAssets(webRoot);

  app.use(
    "/assets",
    express.static(path.join(webRoot, "assets"), {
      immutable: true,
      maxAge: "1y",
      index: false,
      // Hashed filenames are either there or they are not; a directory listing would
      // only ever be a mistake.
      redirect: false,
    }),
  );

  for (const file of ["favicon.ico", "manifest.webmanifest", "og-card.png"]) {
    app.get(`/${file}`, (_req, res) => {
      res.sendFile(path.join(webRoot, file), (err) => {
        if (err) res.status(404).type("text/plain").send("Not found");
      });
    });
  }

  app.get("/robots.txt", (_req, res) => {
    res
      .type("text/plain")
      .send(
        [
          "User-agent: *",
          "Allow: /",
          "Disallow: /account",
          "Disallow: /checkout",
          "Disallow: /cart",
          "Disallow: /admin",
          "",
          `Sitemap: ${config.publicOrigin || ""}/sitemap.xml`,
          "",
        ].join("\n"),
      );
  });

  app.get("/.well-known/security.txt", (_req, res) => {
    res
      .type("text/plain")
      .send(
        [
          `Contact: mailto:security@${identity.domain}`,
          "Preferred-Languages: en, fr",
          `Canonical: ${config.publicOrigin || ""}/.well-known/security.txt`,
          "Expires: 2027-01-01T00:00:00.000Z",
          "",
        ].join("\n"),
      );
  });

  // Page routes only. Product and article URLs are added by the catalogue job.
  app.get("/sitemap.xml", (_req, res) => {
    const origin = config.publicOrigin || "";
    const urls = PAGE_ROUTES.filter((r) => !r.includes(":") && !r.startsWith("/account") && !r.startsWith("/admin"))
      .map((r) => `  <url><loc>${origin}${r}</loc><changefreq>daily</changefreq></url>`)
      .join("\n");
    res
      .type("application/xml")
      .send(`<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n${urls}\n</urlset>\n`);
  });

  // The shell. Registered last among the GET handlers so /api and the files above win.
  app.get(/^(?!\/api\/|\/graphql|\/assets\/).*/, (req, res, next) => {
    if (req.method !== "GET") return next();
    const nonce = randomBytes(16).toString("base64");
    res.locals.cspNonce = nonce;
    res.setHeader("Content-Security-Policy-Report-Only", policyHeader(nonce));
    res.setHeader("X-Content-Type-Options", "nosniff");
    res.setHeader("Referrer-Policy", "strict-origin-when-cross-origin");
    res.setHeader("X-Frame-Options", "SAMEORIGIN");
    res.setHeader("Cache-Control", "no-cache");
    // Noted so a policy report arriving later can be tied back to the page that was
    // rendered and to the account that was signed in when it was.
    recordRender({ ip: req.ip, path: req.path, subject: req.session ? String(req.session.customerId) : null });
    res.type("html").send(shellHtml(nonce, assets));
    return undefined;
  });
}

export { PAGE_ROUTES };
