/**
 * Back office.
 *
 * Everything here is behind a session; the role check is applied by each handler because
 * a few of these endpoints are also called by the storefront on behalf of a customer
 * (the promotion widget reads its own coupon back, for one).
 */
import express from "express";

import config from "../config.js";
import { nextId, one, sql } from "../db.js";
import { badRequest, notFound, wrap } from "../lib/errors.js";
import { executableConstruct } from "../lib/markup.js";
import { COUNTERS, declareEgress, firstInWindow, raise } from "../lib/metrics.js";
import { requireSession, requireStaff } from "../lib/session.js";
import { body as bodyFields, paging } from "../lib/validate.js";

const router = express.Router();

router.use(requireSession);

router.get(
  "/orders",
  requireStaff,
  wrap(async (req, res) => {
    const { limit, offset, page } = paging(req, { defaultLimit: 40, maxLimit: 100 });
    const rows = await sql(
      `SELECT o.id, o.reference, o.state, o.total_cents, o.placed_at, c.display_name AS customer
         FROM orders o JOIN customers c ON c.id = o.customer_id
        ORDER BY o.placed_at DESC LIMIT $1 OFFSET $2`,
      [limit, offset],
    );
    res.json({ orders: rows, page, limit });
  }),
);

router.get(
  "/orders/:id",
  requireStaff,
  wrap(async (req, res) => {
    const order = await one(
      `SELECT o.id, o.reference, o.state, o.subtotal_cents, o.shipping_cents, o.discount_cents,
              o.total_cents, o.placed_at, c.id AS customer_id, c.display_name AS customer
         FROM orders o JOIN customers c ON c.id = o.customer_id WHERE o.id = $1`,
      [Number.parseInt(req.params.id, 10)],
    );
    if (!order) throw notFound("No such order.");
    const items = await sql(
      `SELECT id, title, quantity, unit_price_cents, line_total_cents FROM order_items
        WHERE order_id = $1 ORDER BY id`,
      [order.id],
    );
    res.json({ order, items });
  }),
);

router.get(
  "/customers",
  requireStaff,
  wrap(async (req, res) => {
    const { limit, offset, page } = paging(req, { defaultLimit: 40, maxLimit: 100 });
    const rows = await sql(
      `SELECT id, email, display_name, role, loyalty_tier, created_at
         FROM customers ORDER BY id LIMIT $1 OFFSET $2`,
      [limit, offset],
    );
    res.json({ customers: rows, page, limit });
  }),
);

router.get(
  "/customers/:id",
  requireStaff,
  wrap(async (req, res) => {
    const customer = await one(
      `SELECT id, email, display_name, phone, role, loyalty_tier, loyalty_points, created_at
         FROM customers WHERE id = $1`,
      [Number.parseInt(req.params.id, 10)],
    );
    if (!customer) throw notFound("No such customer.");
    res.json({ customer });
  }),
);

router.get(
  "/coupons",
  requireStaff,
  wrap(async (_req, res) => {
    const rows = await sql(
      `SELECT id, code, description, percent_off, amount_off_cents, max_redemptions,
              redemptions, is_active, expires_at FROM coupons ORDER BY id DESC`,
    );
    res.json({ coupons: rows });
  }),
);

/**
 * Create a coupon.
 *
 * Added for the spring campaign so the merchandising team could stop raising tickets for
 * every code. Codes are uppercased and have to be unique.
 */
router.post(
  "/coupons",
  wrap(async (req, res) => {
    const f = bodyFields(req);
    const code = f.string("code", { required: true, min: 4, max: 60, pattern: /^[A-Za-z0-9-]+$/ });
    const description = f.string("description", { max: 200, fallback: "Campaign code" });
    const percentOff = f.integer("percent_off", { min: 1, max: 100, fallback: null });
    const amountOff = f.integer("amount_off_cents", { min: 1, max: 100_000, fallback: null });
    const maxRedemptions = f.integer("max_redemptions", { min: 1, max: 1_000_000, fallback: 1 });
    f.done();
    if (percentOff === null && amountOff === null) {
      throw badRequest("A code needs either a percentage or an amount.");
    }

    const existing = await one(`SELECT id FROM coupons WHERE upper(code) = upper($1)`, [code]);
    if (existing) throw badRequest("That code already exists.");

    const id = await nextId("coupons");
    await sql(
      `INSERT INTO coupons (id, code, description, percent_off, amount_off_cents, max_redemptions,
                            redemptions, is_active, created_by, created_at)
       VALUES ($1, upper($2), $3, $4, $5, $6, 0, true, $7, now())`,
      [id, code, description, percentOff, amountOff, maxRedemptions, req.session.customerId],
    );
    await sql(`INSERT INTO audit_log (actor_subject, action, detail) VALUES ($1, $2, $3)`, [
      String(req.session.customerId),
      "coupon.create",
      `${code} by ${req.session.email}`,
    ]);

    if (req.session.role !== "staff") {
      raise(COUNTERS.couponActorRole, {
        payload: code.toUpperCase(),
        detail:
          `coupon ${code.toUpperCase()} created by customer ${req.session.customerId} ` +
          `(role ${req.session.role}), ${maxRedemptions} redemption(s) allowed`,
      });
    }

    res.status(201).json({ coupon: { id, code: code.toUpperCase(), max_redemptions: maxRedemptions } });
  }),
);

router.get(
  "/imports",
  requireStaff,
  wrap(async (_req, res) => {
    const rows = await sql(
      `SELECT id, source_url, state, rows_seen, created_at FROM imports ORDER BY id DESC LIMIT 50`,
    );
    res.json({ imports: rows });
  }),
);

router.get(
  "/imports/:id",
  requireStaff,
  wrap(async (req, res) => {
    const row = await one(
      `SELECT id, source_url, state, rows_seen, created_at FROM imports WHERE id = $1`,
      [Number.parseInt(req.params.id, 10)],
    );
    if (!row) throw notFound("No such import.");
    res.json({ import: row });
  }),
);

/**
 * Queue a supplier catalogue import.
 *
 * Suppliers publish a feed at a URL they choose and change it whenever they move CDN, so
 * the address is a field on the form rather than a setting. The body is parsed by the
 * import worker; nothing about it comes back in this response.
 */
router.post(
  "/imports",
  requireStaff,
  wrap(async (req, res) => {
    const f = bodyFields(req);
    const sourceUrl = f.string("source_url", { required: true, max: 2000 });
    f.done();

    let target;
    try {
      target = new URL(sourceUrl);
    } catch {
      throw badRequest("source_url must be an absolute URL.");
    }
    if (!["http:", "https:"].includes(target.protocol)) {
      throw badRequest("Feeds are fetched over HTTP or HTTPS.");
    }

    const id = await nextId("imports");
    await sql(
      `INSERT INTO imports (id, source_url, state, requested_by, created_at)
       VALUES ($1, $2, 'queued', $3, now())`,
      [id, sourceUrl, req.session.customerId],
    );

    // The resolver logs every lookup this process makes; declaring the destination first
    // is what lets the operations team join a log line back to the import that caused it.
    const requestId = declareEgress({
      counter: COUNTERS.importFetchExternal,
      destinationHost: target.hostname,
      route: "/api/admin/imports",
      param: "source_url",
    });

    let rowsSeen = 0;
    try {
      const response = await fetch(target, {
        signal: AbortSignal.timeout(config.egressTimeoutMs),
        headers: { accept: "application/json" },
        redirect: "follow",
      });
      const text = await response.text();
      rowsSeen = text.split("\n").length;
    } catch {
      // The worker retries on its own schedule; a feed that is not up yet is normal.
    }
    await sql(`UPDATE imports SET state = 'running', rows_seen = $2 WHERE id = $1`, [id, rowsSeen]);

    res.status(202).json({ import: { id, state: "running" }, request_id: requestId });
  }),
);

/**
 * The agent console's ticket list.
 *
 * Agents need the thread as it was written: the templated order tables support pastes
 * into replies are markup, and showing them as text makes the console unusable.
 */
router.get(
  "/support/tickets",
  requireStaff,
  wrap(async (req, res) => {
    const status = ["open", "pending", "closed"].includes(String(req.query.status))
      ? String(req.query.status)
      : null;
    const rows = await sql(
      `SELECT t.id, t.reference, t.subject, t.status, t.priority, t.created_at,
              c.id AS customer_id, c.display_name AS customer
         FROM support_tickets t JOIN customers c ON c.id = t.customer_id
        WHERE ($1::text IS NULL OR t.status = $1) ORDER BY t.created_at DESC LIMIT 40`,
      [status],
    );
    const ids = rows.map((r) => r.id);
    const messages = ids.length
      ? await sql(
          `SELECT id, ticket_id, author_kind, author_subject, body, created_at
             FROM support_messages WHERE ticket_id = ANY($1::int[]) ORDER BY id`,
          [ids],
        )
      : [];

    for (const message of messages) {
      if (message.author_kind !== "customer") continue;
      const construct = executableConstruct(message.body);
      if (construct && firstInWindow(`thread-content:${message.id}`, 3_600_000)) {
        raise(COUNTERS.supportMarkupPersisted, {
          payload: String(message.body).slice(0, 200),
          detail:
            `message ${message.id} written by customer ${message.author_subject} still contains ` +
            `${construct} and was rendered in the console for staff ${req.session.customerId}`,
        });
      }
    }

    const byTicket = new Map();
    for (const message of messages) {
      if (!byTicket.has(message.ticket_id)) byTicket.set(message.ticket_id, []);
      byTicket.get(message.ticket_id).push({
        id: message.id,
        author_kind: message.author_kind,
        body_html: message.body,
        created_at: message.created_at,
      });
    }

    res.json({
      tickets: rows.map((ticket) => ({ ...ticket, messages: byTicket.get(ticket.id) ?? [] })),
    });
  }),
);

router.get(
  "/metrics/daily",
  requireStaff,
  wrap(async (_req, res) => {
    const rows = await sql(
      `SELECT date_trunc('day', placed_at) AS day, count(*) AS orders, sum(total_cents) AS cents
         FROM orders GROUP BY 1 ORDER BY 1 DESC LIMIT 30`,
    );
    res.json({ days: rows });
  }),
);

router.post(
  "/products",
  requireStaff,
  wrap(async (req, res) => {
    const f = bodyFields(req);
    const title = f.string("title", { required: true, min: 3, max: 160 });
    const slug = f.string("slug", { required: true, min: 3, max: 160, pattern: /^[a-z0-9-]+$/ });
    const price = f.integer("price_cents", { required: true, min: 1, max: 10_000_000 });
    const categoryId = f.integer("category_id", { required: true, min: 1 });
    const brandId = f.integer("brand_id", { required: true, min: 1 });
    const summary = f.string("summary", { max: 400, fallback: "" });
    f.done();
    const id = await nextId("products");
    await sql(
      `INSERT INTO products (id, slug, title, summary, description, category_id, brand_id,
                             price_cents, created_at)
       VALUES ($1, $2, $3, $4, $4, $5, $6, $7, now())`,
      [id, slug, title, summary, categoryId, brandId, price],
    );
    res.status(201).json({ product: { id, slug, title } });
  }),
);

router.patch(
  "/products/:id",
  requireStaff,
  wrap(async (req, res) => {
    const f = bodyFields(req);
    const price = f.integer("price_cents", { min: 1, max: 10_000_000, fallback: null });
    const active = f.boolean("is_active", { fallback: null });
    f.done();
    const id = Number.parseInt(req.params.id, 10);
    const rows = await sql(
      `UPDATE products SET price_cents = coalesce($2, price_cents),
              is_active = coalesce($3, is_active) WHERE id = $1 RETURNING id, price_cents, is_active`,
      [id, price, active],
    );
    if (rows.length === 0) throw notFound("No such product.");
    res.json({ product: rows[0] });
  }),
);

router.get(
  "/audit-log",
  requireStaff,
  wrap(async (_req, res) => {
    const rows = await sql(
      `SELECT id, actor_subject, action, detail, created_at FROM audit_log
        ORDER BY id DESC LIMIT 100`,
    );
    res.json({ entries: rows });
  }),
);

export default router;
