/**
 * The account area.
 *
 * Everything a signed-in customer owns: their details, their addresses, their saved
 * searches and the dashboard they arrange themselves. The loyalty endpoints also answer
 * to the partner's bearer token, which is why the session guard is per handler here.
 */
import fs from "node:fs/promises";
import path from "node:path";

import express from "express";
import multer from "multer";

import config from "../config.js";
import { nextId, one, sql, unsafe } from "../db.js";
import { badRequest, notFound, unauthorized, wrap } from "../lib/errors.js";
import { readLoyaltyToken } from "../lib/loyaltytoken.js";
import { COUNTERS, declareEgress, raise } from "../lib/metrics.js";
import { resultEscaped, statementWidened } from "../lib/planwatch.js";
import { requireSession } from "../lib/session.js";
import { body as bodyFields, paging } from "../lib/validate.js";

const router = express.Router();

// Avatars are held in memory and written under a name we choose. multer only keeps the
// basename of `originalname` unless `preservePath` is set, but nothing here uses the
// client's name at all: the extension comes from the sniffed content type.
const upload = multer({
  storage: multer.memoryStorage(),
  limits: { fileSize: 2 * 1024 * 1024, files: 1 },
});

router.get(
  "/profile",
  requireSession,
  wrap(async (req, res) => {
    const customer = await one(
      `SELECT id, email, given_name, family_name, display_name, phone, loyalty_tier,
              loyalty_points, avatar_url, marketing_opt_in, created_at
         FROM customers WHERE id = $1`,
      [req.session.customerId],
    );
    if (!customer) throw notFound("We cannot find your account.");
    res.json({ customer });
  }),
);

router.patch(
  "/profile",
  requireSession,
  wrap(async (req, res) => {
    const f = bodyFields(req);
    const given = f.string("given_name", { max: 60, fallback: null });
    const family = f.string("family_name", { max: 60, fallback: null });
    const phone = f.string("phone", { max: 32, pattern: /^[0-9+ ()-]+$/, fallback: null });
    const marketing = f.boolean("marketing_opt_in", { fallback: null });
    f.done();
    const rows = await sql(
      `UPDATE customers SET given_name = coalesce($2, given_name),
              family_name = coalesce($3, family_name),
              display_name = coalesce($2, given_name) || ' ' || coalesce($3, family_name),
              phone = coalesce($4, phone),
              marketing_opt_in = coalesce($5, marketing_opt_in)
        WHERE id = $1
       RETURNING id, given_name, family_name, display_name, phone, marketing_opt_in`,
      [req.session.customerId, given, family, phone, marketing],
    );
    res.json({ customer: rows[0] });
  }),
);

const WIDGET_SIZES = ["narrow", "wide", "full"];

router.get(
  "/preferences",
  requireSession,
  wrap(async (req, res) => {
    const row = await one(
      `SELECT locale, currency, theme, widgets, updated_at FROM account_preferences WHERE customer_id = $1`,
      [req.session.customerId],
    );
    res.json({
      preferences: row ?? { locale: "en-GB", currency: "EUR", theme: "system", widgets: [] },
    });
  }),
);

/**
 * Save the dashboard arrangement.
 *
 * Widget titles are the customer's own words for their own dashboard — "Bits for the
 * allotment", "Presents (don't show Sam)" — and the editor offers the same light
 * formatting as the rest of the site, so they are stored as written.
 */
router.put(
  "/preferences",
  requireSession,
  wrap(async (req, res) => {
    const f = bodyFields(req);
    const locale = f.string("locale", { max: 12, pattern: /^[a-z]{2}(-[A-Z]{2})?$/, fallback: null });
    const currency = f.oneOf("currency", ["EUR", "GBP", "SEK", "DKK"], { fallback: null });
    const theme = f.oneOf("theme", ["system", "light", "dark"], { fallback: null });
    f.done();

    const incoming = Array.isArray(req.body?.widgets) ? req.body.widgets : null;
    let widgets = null;
    if (incoming) {
      if (incoming.length > 12) throw badRequest("A dashboard holds up to twelve panels.");
      widgets = incoming.map((widget, index) => {
        const id = String(widget?.id ?? `panel-${index}`).slice(0, 40);
        const title = String(widget?.title ?? "").slice(0, 200);
        const size = WIDGET_SIZES.includes(widget?.size) ? widget.size : "narrow";
        return { id, title, size };
      });
    }

    const rows = await sql(
      `INSERT INTO account_preferences (customer_id, locale, currency, theme, widgets, updated_at)
       VALUES ($1, coalesce($2, 'en-GB'), coalesce($3, 'EUR'), coalesce($4, 'system'),
               coalesce($5::jsonb, '[]'::jsonb), now())
       ON CONFLICT (customer_id) DO UPDATE SET
            locale = coalesce($2, account_preferences.locale),
            currency = coalesce($3, account_preferences.currency),
            theme = coalesce($4, account_preferences.theme),
            widgets = coalesce($5::jsonb, account_preferences.widgets),
            updated_at = now()
       RETURNING locale, currency, theme, widgets, updated_at`,
      [
        req.session.customerId,
        locale,
        currency,
        theme,
        widgets === null ? null : JSON.stringify(widgets),
      ],
    );
    res.json({ preferences: rows[0] });
  }),
);

router.get(
  "/addresses",
  requireSession,
  wrap(async (req, res) => {
    const rows = await sql(
      `SELECT id, label, recipient, line1, line2, city, postcode, country, is_default
         FROM addresses WHERE customer_id = $1 ORDER BY is_default DESC, id`,
      [req.session.customerId],
    );
    res.json({ addresses: rows });
  }),
);

router.post(
  "/addresses",
  requireSession,
  wrap(async (req, res) => {
    const f = bodyFields(req);
    const label = f.string("label", { required: true, max: 40 });
    const recipient = f.string("recipient", { required: true, max: 120 });
    const line1 = f.string("line1", { required: true, max: 160 });
    const line2 = f.string("line2", { max: 160, fallback: null });
    const city = f.string("city", { required: true, max: 80 });
    const postcode = f.string("postcode", { required: true, max: 16 });
    const country = f.string("country", { required: true, min: 2, max: 2, pattern: /^[A-Z]{2}$/ });
    f.done();
    const id = await nextId("addresses");
    await sql(
      `INSERT INTO addresses (id, customer_id, label, recipient, line1, line2, city, postcode, country)
       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)`,
      [id, req.session.customerId, label, recipient, line1, line2, city, postcode, country],
    );
    res.status(201).json({ address: { id, label, city, postcode, country } });
  }),
);

router.patch(
  "/addresses/:id",
  requireSession,
  wrap(async (req, res) => {
    const f = bodyFields(req);
    const label = f.string("label", { max: 40, fallback: null });
    const isDefault = f.boolean("is_default", { fallback: null });
    f.done();
    const id = Number.parseInt(req.params.id, 10);
    const rows = await sql(
      `UPDATE addresses SET label = coalesce($3, label), is_default = coalesce($4, is_default)
        WHERE id = $1 AND customer_id = $2 RETURNING id, label, is_default`,
      [id, req.session.customerId, label, isDefault],
    );
    if (rows.length === 0) throw notFound("That address is not on your account.");
    res.json({ address: rows[0] });
  }),
);

router.delete(
  "/addresses/:id",
  requireSession,
  wrap(async (req, res) => {
    const id = Number.parseInt(req.params.id, 10);
    await sql(`DELETE FROM addresses WHERE id = $1 AND customer_id = $2`, [
      id,
      req.session.customerId,
    ]);
    res.json({ status: "removed" });
  }),
);

router.get(
  "/payment-methods",
  requireSession,
  wrap(async (req, res) => {
    const rows = await sql(
      `SELECT id, brand, last4, exp_month, exp_year, is_default FROM payment_methods
        WHERE customer_id = $1 ORDER BY is_default DESC, id`,
      [req.session.customerId],
    );
    res.json({ payment_methods: rows });
  }),
);

router.delete(
  "/payment-methods/:id",
  requireSession,
  wrap(async (req, res) => {
    await sql(`DELETE FROM payment_methods WHERE id = $1 AND customer_id = $2`, [
      Number.parseInt(req.params.id, 10),
      req.session.customerId,
    ]);
    res.json({ status: "removed" });
  }),
);

router.get(
  "/orders",
  requireSession,
  wrap(async (req, res) => {
    const { limit, offset, page } = paging(req, { defaultLimit: 20, maxLimit: 50 });
    const rows = await sql(
      `SELECT id, reference, state, total_cents, placed_at FROM orders
        WHERE customer_id = $1 ORDER BY placed_at DESC LIMIT $2 OFFSET $3`,
      [req.session.customerId, limit, offset],
    );
    res.json({ orders: rows, page, limit });
  }),
);

router.get(
  "/wishlist",
  requireSession,
  wrap(async (req, res) => {
    const rows = await sql(
      `SELECT wi.id, wi.variant_id, p.slug, p.title, v.option_value, v.price_cents
         FROM wishlists w JOIN wishlist_items wi ON wi.wishlist_id = w.id
         JOIN variants v ON v.id = wi.variant_id JOIN products p ON p.id = v.product_id
        WHERE w.customer_id = $1 ORDER BY wi.id`,
      [req.session.customerId],
    );
    res.json({ items: rows });
  }),
);

router.post(
  "/wishlist/items",
  requireSession,
  wrap(async (req, res) => {
    const f = bodyFields(req);
    const variantId = f.integer("variant_id", { required: true, min: 1 });
    f.done();
    let list = await one(`SELECT id FROM wishlists WHERE customer_id = $1 ORDER BY id LIMIT 1`, [
      req.session.customerId,
    ]);
    if (!list) {
      const listId = await nextId("wishlists");
      await sql(
        `INSERT INTO wishlists (id, customer_id, name, created_at) VALUES ($1, $2, 'Saved for later', now())`,
        [listId, req.session.customerId],
      );
      list = { id: listId };
    }
    const variant = await one(`SELECT id FROM variants WHERE id = $1`, [variantId]);
    if (!variant) throw notFound("We no longer stock that option.");
    const id = await nextId("wishlist_items");
    await sql(
      `INSERT INTO wishlist_items (id, wishlist_id, variant_id, added_at) VALUES ($1, $2, $3, now())`,
      [id, list.id, variantId],
    );
    res.status(201).json({ item: { id, variant_id: variantId } });
  }),
);

router.delete(
  "/wishlist/items/:id",
  requireSession,
  wrap(async (req, res) => {
    await sql(
      `DELETE FROM wishlist_items wi USING wishlists w
        WHERE wi.wishlist_id = w.id AND wi.id = $1 AND w.customer_id = $2`,
      [Number.parseInt(req.params.id, 10), req.session.customerId],
    );
    res.json({ status: "removed" });
  }),
);

/**
 * Saved searches.
 *
 * A rule is a list of `key:value` terms separated by semicolons, which is what the
 * filter bar produces. It is stored as written and compiled when the search is run.
 */
router.get(
  "/saved-searches",
  requireSession,
  wrap(async (req, res) => {
    const rows = await sql(
      `SELECT id, label, rule, created_at FROM saved_searches WHERE customer_id = $1 ORDER BY id`,
      [req.session.customerId],
    );
    res.json({ saved_searches: rows });
  }),
);

router.post(
  "/saved-searches",
  requireSession,
  wrap(async (req, res) => {
    const f = bodyFields(req);
    const label = f.string("label", { required: true, min: 2, max: 80 });
    const rule = f.string("rule", { required: true, min: 2, max: 500 });
    f.done();
    const id = await nextId("saved_searches");
    await sql(
      `INSERT INTO saved_searches (id, customer_id, label, rule, created_at)
       VALUES ($1, $2, $3, $4, now())`,
      [id, req.session.customerId, label, rule],
    );
    res.status(201).json({ saved_search: { id, label, rule } });
  }),
);

router.delete(
  "/saved-searches/:id",
  requireSession,
  wrap(async (req, res) => {
    await sql(`DELETE FROM saved_searches WHERE id = $1 AND customer_id = $2`, [
      Number.parseInt(req.params.id, 10),
      req.session.customerId,
    ]);
    res.json({ status: "removed" });
  }),
);

/**
 * Compile a stored rule into a filter.
 *
 * Terms the compiler does not recognise become a title match, which is what makes a rule
 * anybody typed by hand still return something.
 */
function compileRule(rule) {
  const clauses = ["p.is_active"];
  const controlClauses = ["p.is_active"];
  const values = [];
  for (const term of String(rule).split(";").map((t) => t.trim()).filter(Boolean)) {
    const price = /^price<(\d+)$/.exec(term);
    if (price) {
      clauses.push(`p.price_cents < ${Number(price[1])}`);
      values.push(Number(price[1]));
      controlClauses.push(`p.price_cents < $${values.length}`);
      continue;
    }
    if (term.startsWith("tag:")) {
      const tag = term.slice(4);
      clauses.push(`p.tag = '${tag}'`);
      values.push(tag);
      controlClauses.push(`p.tag = $${values.length}`);
      continue;
    }
    if (term.startsWith("category:")) {
      const slug = term.slice(9);
      clauses.push(`p.category_id = (SELECT id FROM categories WHERE slug = '${slug}')`);
      values.push(slug);
      controlClauses.push(`p.category_id = (SELECT id FROM categories WHERE slug = $${values.length})`);
      continue;
    }
    clauses.push(`p.title ILIKE '%${term}%'`);
    values.push(`%${term}%`);
    controlClauses.push(`p.title ILIKE $${values.length}`);
  }
  const columns = "p.id, p.slug, p.title, p.price_cents, p.rating_count";
  return {
    statement: `SELECT ${columns} FROM products p WHERE ${clauses.join(" AND ")} ORDER BY p.rating_count DESC LIMIT 48`,
    template: `SELECT ${columns} FROM products p WHERE ${controlClauses.join(" AND ")} ORDER BY p.rating_count DESC LIMIT 48`,
    control: `SELECT p.id FROM products p WHERE ${controlClauses.join(" AND ")} LIMIT 48`,
    values,
  };
}

router.get(
  "/saved-searches/:id/results",
  requireSession,
  wrap(async (req, res) => {
    const saved = await one(
      `SELECT id, label, rule FROM saved_searches WHERE id = $1 AND customer_id = $2`,
      [Number.parseInt(req.params.id, 10), req.session.customerId],
    );
    if (!saved) throw notFound("That saved search no longer exists.");

    const compiled = compileRule(saved.rule);
    const result = await unsafe(compiled.statement);

    if (statementWidened(compiled.statement, compiled.template)) {
      const control = await sql(compiled.control, compiled.values);
      const escaped = resultEscaped(result.rows, control.map((r) => r.id));
      if (escaped) {
        raise(COUNTERS.savedSearchPlanAnomaly, {
          payload: String(saved.rule).slice(0, 300),
          detail:
            `saved rule ${saved.id} compiled to a plan returning ${escaped.returned} row(s) where ` +
            `the parameterised form matches ${escaped.expected}; keys outside products: ` +
            `${escaped.foreign.join(",") || "none"}`,
        });
      }
    }

    res.json({ saved_search: { id: saved.id, label: saved.label }, products: result.rows });
  }),
);

/**
 * Loyalty balance.
 *
 * Answers to a session, and to the partner's bearer token for their own apps.
 */
router.get(
  "/loyalty",
  wrap(async (req, res) => {
    const token = readLoyaltyToken(req.headers.authorization);
    const subject = token ? Number.parseInt(token.claims.sub, 10) : req.session?.customerId;
    if (!Number.isFinite(subject)) throw unauthorized();

    const customer = await one(
      `SELECT id, display_name, loyalty_tier, loyalty_points FROM customers WHERE id = $1`,
      [subject],
    );
    if (!customer) throw notFound("No loyalty record for that member.");

    // Recorded here rather than in the reader: what matters is a token that actually got
    // somebody else's balance out of the service, not one that was merely well formed.
    if (token?.unverified) {
      raise(COUNTERS.tokenUnverifiedAccept, {
        payload: String(req.headers.authorization ?? "").slice(0, 140),
        detail:
          `the balance of member ${customer.id} was served from a bearer token whose ` +
          `signature was never checked`,
      });
    }
    if (token?.keyOutsideDirectory) {
      raise(COUNTERS.tokenKeyPathEscape, {
        payload: String(token.kid ?? ""),
        detail:
          `the balance of member ${customer.id} was served from a token verified against ` +
          `${token.keyPath}, which is outside ${config.loyaltyKeyDir}`,
      });
    }

    res.json({
      loyalty: {
        customer_id: customer.id,
        member: customer.display_name,
        tier: customer.loyalty_tier,
        points: customer.loyalty_points,
      },
    });
  }),
);

router.get(
  "/loyalty/transactions",
  requireSession,
  wrap(async (req, res) => {
    const rows = await sql(
      `SELECT id, points, reason, created_at FROM loyalty_transactions
        WHERE customer_id = $1 ORDER BY created_at DESC LIMIT 50`,
      [req.session.customerId],
    );
    res.json({ transactions: rows });
  }),
);

router.get(
  "/notifications",
  requireSession,
  wrap(async (req, res) => {
    const rows = await sql(
      `SELECT id, kind, body, read_at, created_at FROM notifications
        WHERE customer_id = $1 ORDER BY created_at DESC LIMIT 50`,
      [req.session.customerId],
    );
    res.json({ notifications: rows });
  }),
);

router.patch(
  "/notifications/:id",
  requireSession,
  wrap(async (req, res) => {
    const rows = await sql(
      `UPDATE notifications SET read_at = now() WHERE id = $1 AND customer_id = $2 RETURNING id, read_at`,
      [Number.parseInt(req.params.id, 10), req.session.customerId],
    );
    if (rows.length === 0) throw notFound("No such notification.");
    res.json({ notification: rows[0] });
  }),
);

router.get(
  "/sessions",
  requireSession,
  wrap(async (req, res) => {
    const rows = await sql(
      `SELECT sid, created_at, expires_at, user_agent FROM sessions WHERE customer_id = $1
        ORDER BY created_at DESC`,
      [req.session.customerId],
    );
    res.json({
      sessions: rows.map((row) => ({
        id: row.sid.slice(0, 8),
        current: row.sid === req.session.sid,
        created_at: row.created_at,
        expires_at: row.expires_at,
        user_agent: row.user_agent,
      })),
    });
  }),
);

router.delete(
  "/sessions/:id",
  requireSession,
  wrap(async (req, res) => {
    await sql(`DELETE FROM sessions WHERE customer_id = $1 AND left(sid, 8) = $2 AND sid <> $3`, [
      req.session.customerId,
      String(req.params.id).slice(0, 8),
      req.session.sid,
    ]);
    res.json({ status: "signed out" });
  }),
);

const IMAGE_SIGNATURES = [
  { ext: "png", bytes: [0x89, 0x50, 0x4e, 0x47] },
  { ext: "jpg", bytes: [0xff, 0xd8, 0xff] },
  { ext: "gif", bytes: [0x47, 0x49, 0x46, 0x38] },
  { ext: "webp", bytes: [0x52, 0x49, 0x46, 0x46] },
];

const sniff = (buffer) =>
  IMAGE_SIGNATURES.find((sig) => sig.bytes.every((b, i) => buffer[i] === b)) ?? null;

router.post(
  "/avatar",
  requireSession,
  upload.single("file"),
  wrap(async (req, res) => {
    if (!req.file) throw badRequest("Attach an image.");
    const kind = sniff(req.file.buffer);
    if (!kind) throw badRequest("That file is not an image we can read.");
    const name = `a${req.session.customerId}.${kind.ext}`;
    await fs.mkdir(config.mediaDir, { recursive: true });
    await fs.writeFile(path.join(config.mediaDir, name), req.file.buffer);
    const url = `/media/${name}`;
    await sql(`UPDATE customers SET avatar_url = $2 WHERE id = $1`, [req.session.customerId, url]);
    res.status(201).json({ avatar_url: url });
  }),
);

/**
 * Import an avatar the customer already has somewhere else.
 *
 * The mobile app uses this instead of uploading: the picture is usually already on a
 * public profile and re-uploading it over a phone connection is slow.
 */
router.post(
  "/avatar/import",
  requireSession,
  wrap(async (req, res) => {
    const f = bodyFields(req);
    const imageUrl = f.string("image_url", { required: true, max: 2000 });
    f.done();

    let target;
    try {
      target = new URL(imageUrl);
    } catch {
      throw badRequest("image_url must be an absolute URL.");
    }
    if (!["http:", "https:"].includes(target.protocol)) {
      throw badRequest("Pictures are fetched over HTTP or HTTPS.");
    }

    const requestId = declareEgress({
      counter: COUNTERS.avatarFetchExternal,
      destinationHost: target.hostname,
      route: "/api/account/avatar/import",
      param: "image_url",
    });

    let stored = null;
    try {
      const response = await fetch(target, {
        signal: AbortSignal.timeout(config.egressTimeoutMs),
        redirect: "follow",
      });
      const buffer = Buffer.from(await response.arrayBuffer());
      const kind = sniff(buffer);
      if (kind) {
        const name = `a${req.session.customerId}.${kind.ext}`;
        await fs.mkdir(config.mediaDir, { recursive: true });
        await fs.writeFile(path.join(config.mediaDir, name), buffer.subarray(0, 2 * 1024 * 1024));
        stored = `/media/${name}`;
        await sql(`UPDATE customers SET avatar_url = $2 WHERE id = $1`, [
          req.session.customerId,
          stored,
        ]);
      }
    } catch {
      // Same answer either way: the picture is fetched again by the media worker, and
      // telling the caller why a remote host refused us is not useful to them.
    }

    res.status(202).json({ status: "queued", avatar_url: stored, request_id: requestId });
  }),
);

export default router;
