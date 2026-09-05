/**
 * Editorial content and the shop's physical estate: the pages marketing edits, the
 * banners on the home page, the shops and the delivery options.
 */
import express from "express";

import { one, sql } from "../db.js";
import { notFound, wrap } from "../lib/errors.js";
import { body as bodyFields } from "../lib/validate.js";
import { SHIPPING_RATES } from "./checkout.js";

const router = express.Router();

router.get(
  "/content/pages/:slug",
  wrap(async (req, res) => {
    const page = await one(
      `SELECT slug, title, body, updated_at FROM content_pages WHERE slug = $1`,
      [req.params.slug],
    );
    if (!page) throw notFound("That page has moved.");
    res.json({ page });
  }),
);

router.get(
  "/content/pages",
  wrap(async (_req, res) => {
    const rows = await sql(`SELECT slug, title, updated_at FROM content_pages ORDER BY title`);
    res.json({ pages: rows });
  }),
);

router.get(
  "/content/banners",
  wrap(async (_req, res) => {
    const rows = await sql(
      `SELECT slug, headline, body, cta_url, position FROM banners ORDER BY position`,
    );
    res.json({ banners: rows });
  }),
);

router.get(
  "/content/faq",
  wrap(async (_req, res) => {
    const rows = await sql(
      `SELECT slug, title, category, body FROM support_articles WHERE category = 'faq' ORDER BY title`,
    );
    res.json({ questions: rows });
  }),
);

router.get(
  "/stores",
  wrap(async (_req, res) => {
    const rows = await sql(`SELECT id, slug, name, city, street, phone FROM stores ORDER BY city`);
    res.json({ stores: rows });
  }),
);

router.get(
  "/stores/:id",
  wrap(async (req, res) => {
    const store = await one(
      `SELECT id, slug, name, city, street, phone FROM stores WHERE id = $1 OR slug = $2`,
      [Number.parseInt(req.params.id, 10) || 0, req.params.id],
    );
    if (!store) throw notFound("We do not have a shop there.");
    const hours = await sql(
      `SELECT weekday, opens, closes FROM store_hours WHERE store_id = $1 ORDER BY weekday`,
      [store.id],
    );
    res.json({ store, hours });
  }),
);

router.get(
  "/stores/:id/hours",
  wrap(async (req, res) => {
    const rows = await sql(
      `SELECT h.weekday, h.opens, h.closes FROM store_hours h JOIN stores s ON s.id = h.store_id
        WHERE s.id = $1 OR s.slug = $2 ORDER BY h.weekday`,
      [Number.parseInt(req.params.id, 10) || 0, req.params.id],
    );
    res.json({ hours: rows });
  }),
);

router.get("/shipping/methods", (_req, res) => {
  res.json({
    methods: Object.entries(SHIPPING_RATES).map(([code, value]) => ({ code, ...value })),
  });
});

router.get(
  "/shipping/estimate",
  wrap(async (req, res) => {
    const country = String(req.query.country ?? "NL").toUpperCase().slice(0, 2);
    const zone = ["NL", "BE", "LU"].includes(country) ? "home" : "eu";
    res.json({
      country,
      zone,
      methods: Object.entries(SHIPPING_RATES).map(([code, value]) => ({
        code,
        label: value.label,
        rate_cents: zone === "home" ? value.rate_cents : value.rate_cents + 600,
      })),
    });
  }),
);

router.post(
  "/newsletter/subscriptions",
  wrap(async (req, res) => {
    const f = bodyFields(req);
    const email = f.email("email", { required: true });
    const locale = f.string("locale", { max: 12, fallback: "en-GB" });
    f.done();
    await sql(`INSERT INTO newsletter_subscriptions (email, locale) VALUES ($1, $2)`, [email, locale]);
    res.status(201).json({ status: "subscribed" });
  }),
);

router.post(
  "/contact-messages",
  wrap(async (req, res) => {
    const f = bodyFields(req);
    const name = f.string("name", { required: true, max: 120 });
    const email = f.email("email", { required: true });
    const message = f.string("body", { required: true, min: 10, max: 4000 });
    f.done();
    await sql(`INSERT INTO contact_messages (name, email, body) VALUES ($1, $2, $3)`, [
      name,
      email,
      message,
    ]);
    res.status(202).json({ status: "received" });
  }),
);

export default router;
