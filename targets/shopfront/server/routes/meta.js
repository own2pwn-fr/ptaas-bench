/**
 * Endpoints that belong to the site rather than to the shop: the health probe the load
 * balancer reads, the small reference lists the client needs at boot, the analytics
 * beacon and the policy report collector.
 */
import express from "express";

import { one, sql } from "../db.js";
import { wrap } from "../lib/errors.js";
import { executableConstruct } from "../lib/markup.js";
import { COUNTERS, firstInWindow, raise } from "../lib/metrics.js";
import { lastRender } from "../lib/navlog.js";

const router = express.Router();

router.get("/status", (_req, res) => {
  res.json({ status: "ok" });
});

router.get("/currencies", (_req, res) => {
  res.json({
    currencies: [
      { code: "EUR", symbol: "€", name: "Euro" },
      { code: "GBP", symbol: "£", name: "Pound sterling" },
      { code: "SEK", symbol: "kr", name: "Swedish krona" },
      { code: "DKK", symbol: "kr", name: "Danish krone" },
    ],
  });
});

router.get("/locales", (_req, res) => {
  res.json({
    locales: [
      { code: "en-GB", label: "English" },
      { code: "nl-NL", label: "Nederlands" },
      { code: "fr-FR", label: "Français" },
      { code: "sv-SE", label: "Svenska" },
    ],
  });
});

/**
 * The analytics beacon.
 *
 * Page views and clicks on tagged elements, sent with sendBeacon so they do not hold a
 * navigation open. Nothing identifying is stored.
 */
router.post(
  "/client-events",
  wrap(async (req, res) => {
    const events = Array.isArray(req.body?.events) ? req.body.events.slice(0, 50) : [];
    for (const event of events) {
      await sql(`INSERT INTO client_events (kind, route, detail) VALUES ($1, $2, $3::jsonb)`, [
        String(event?.kind ?? "view").slice(0, 40),
        String(event?.route ?? "").slice(0, 200),
        JSON.stringify({ t: event?.t ?? null, label: String(event?.label ?? "").slice(0, 120) }),
      ]);
    }
    res.status(204).end();
  }),
);

/**
 * Content security policy reports.
 *
 * The policy runs in report-only mode while the last inline scripts are moved out of the
 * checkout iframe. Reports are kept so the rollout can be finished, and a report about a
 * script the site did not put on the page is looked at the same day: it means a value we
 * stored, or a value we echoed, reached a place where the browser was willing to run it.
 */
const SCRIPT_DIRECTIVES = ["script-src", "script-src-elem", "script-src-attr"];

router.post(
  "/policy-reports",
  wrap(async (req, res) => {
    const reports = normaliseReports(req.body);
    for (const report of reports) {
      const documentUri = String(report["document-uri"] ?? "");
      const directive = String(report["effective-directive"] ?? report["violated-directive"] ?? "");
      const blocked = String(report["blocked-uri"] ?? "");
      const sample = String(report["script-sample"] ?? "").slice(0, 400);

      await sql(
        `INSERT INTO policy_reports (document_uri, directive, blocked_uri, sample, client_ip)
         VALUES ($1, $2, $3, $4, $5)`,
        [documentUri.slice(0, 2000), directive.slice(0, 120), blocked.slice(0, 400), sample, req.ip],
      );

      if (!SCRIPT_DIRECTIVES.some((d) => directive.startsWith(d))) continue;
      await attributeReport(req, documentUri);
    }
    res.status(204).end();
  }),
);

function normaliseReports(payload) {
  if (Array.isArray(payload)) {
    // Reporting API: [{ type, url, body: { documentURL, effectiveDirective, ... } }]
    return payload
      .filter((entry) => entry && entry.type === "csp-violation" && entry.body)
      .map((entry) => ({
        "document-uri": entry.body.documentURL ?? entry.url,
        "effective-directive": entry.body.effectiveDirective,
        "blocked-uri": entry.body.blockedURL,
        "script-sample": entry.body.sample,
      }));
  }
  if (payload && typeof payload === "object" && payload["csp-report"]) return [payload["csp-report"]];
  if (payload && typeof payload === "object" && payload["document-uri"]) return [payload];
  return [];
}

/**
 * Work out what the browser was actually running.
 *
 * A report on its own carries a document URI and nothing else, so it is matched against
 * the renders this process served recently from the same address. Without that match a
 * report says nothing: anything can post to this endpoint.
 */
async function attributeReport(req, documentUri) {
  let url;
  try {
    url = new URL(documentUri, "http://localhost");
  } catch {
    return;
  }
  const render = lastRender(req.ip, url.pathname);
  if (!render) return;

  if (url.pathname === "/search") {
    const crumb = url.searchParams.get("ref") ?? "";
    const construct = executableConstruct(crumb);
    if (!construct) return;
    if (!firstInWindow(`policy-search:${crumb.slice(0, 80)}`, 300_000)) return;
    raise(COUNTERS.searchScriptExecution, {
      payload: crumb.slice(0, 200),
      detail:
        `the browser reported script running on ${url.pathname}, where the crumb parameter ` +
        `carries ${construct}`,
    });
    return;
  }

  if (url.pathname === "/account" || url.pathname.startsWith("/account/")) {
    if (!render.subject) return;
    const row = await one(`SELECT widgets FROM account_preferences WHERE customer_id = $1`, [
      Number.parseInt(render.subject, 10),
    ]);
    const widgets = Array.isArray(row?.widgets) ? row.widgets : [];
    const offending = widgets
      .map((widget) => ({ widget, construct: executableConstruct(widget?.title) }))
      .find((entry) => entry.construct);
    if (!offending) return;
    if (!firstInWindow(`policy-account:${render.subject}`, 300_000)) return;
    raise(COUNTERS.accountScriptExecution, {
      payload: String(offending.widget.title).slice(0, 200),
      detail:
        `the browser reported script running on ${url.pathname} for customer ${render.subject}, ` +
        `whose stored panel title carries ${offending.construct}`,
    });
  }
}

export default router;
