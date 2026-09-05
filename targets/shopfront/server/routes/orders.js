/**
 * Orders.
 *
 * The account area reads from here, and so does the fulfilment console: an order is the
 * same object either way, which is why the state machine lives on this router rather
 * than under /api/admin.
 */
import express from "express";

import { nextId, one, sql } from "../db.js";
import { badRequest, conflict, notFound, wrap } from "../lib/errors.js";
import { COUNTERS, raise } from "../lib/metrics.js";
import { requireSession } from "../lib/session.js";
import { body as bodyFields, paging } from "../lib/validate.js";

const router = express.Router();

/** Which states an order may move to from where it is. */
const TRANSITIONS = {
  placed: ["paid", "cancelled"],
  paid: ["picking", "cancelled", "refunded"],
  picking: ["fulfilled", "cancelled"],
  fulfilled: ["returned", "refunded", "comped"],
  cancelled: [],
  refunded: [],
  comped: [],
  returned: ["refunded"],
};

// Moves that only the fulfilment and finance teams are supposed to make. A customer's
// button on the account page is "cancel", and nothing else.
const STAFF_ONLY = new Set(["refunded", "fulfilled", "comped", "picking", "paid"]);

router.use(requireSession);

router.get(
  "/",
  wrap(async (req, res) => {
    const { limit, offset, page } = paging(req, { defaultLimit: 20, maxLimit: 50 });
    const rows = await sql(
      `SELECT id, reference, state, currency, total_cents, placed_at
         FROM orders WHERE customer_id = $1 ORDER BY placed_at DESC LIMIT $2 OFFSET $3`,
      [req.session.customerId, limit, offset],
    );
    res.json({ orders: rows, page, limit });
  }),
);

/**
 * Order detail.
 *
 * Reached from the account page, where the id always comes out of the list above.
 */
router.get(
  "/:id",
  wrap(async (req, res) => {
    const orderId = Number.parseInt(req.params.id, 10);
    if (!Number.isFinite(orderId)) throw notFound("We cannot find that order.");
    const order = await one(
      `SELECT o.id, o.reference, o.customer_id, o.state, o.currency, o.subtotal_cents,
              o.shipping_cents, o.discount_cents, o.total_cents, o.placed_at,
              a.line1, a.city, a.postcode, a.country, a.recipient
         FROM orders o LEFT JOIN addresses a ON a.id = o.address_id
        WHERE o.id = $1`,
      [orderId],
    );
    if (!order) throw notFound("We cannot find that order.");

    if (String(order.customer_id) !== String(req.session.customerId)) {
      raise(COUNTERS.orderSubjectMismatch, {
        payload: String(orderId),
        detail:
          `order ${order.reference} belongs to customer ${order.customer_id} and was served in ` +
          `full to customer ${req.session.customerId}`,
      });
    }

    const items = await sql(
      `SELECT id, variant_id, title, quantity, unit_price_cents, line_total_cents
         FROM order_items WHERE order_id = $1 ORDER BY id`,
      [orderId],
    );
    res.json({ order, items });
  }),
);

router.get(
  "/:id/items",
  wrap(async (req, res) => {
    const orderId = Number.parseInt(req.params.id, 10);
    const rows = await sql(
      `SELECT i.id, i.variant_id, i.title, i.quantity, i.unit_price_cents, i.line_total_cents
         FROM order_items i JOIN orders o ON o.id = i.order_id
        WHERE i.order_id = $1 AND o.customer_id = $2 ORDER BY i.id`,
      [orderId, req.session.customerId],
    );
    if (rows.length === 0) throw notFound("We cannot find that order.");
    res.json({ items: rows });
  }),
);

router.get(
  "/:id/shipments",
  wrap(async (req, res) => {
    const orderId = Number.parseInt(req.params.id, 10);
    const rows = await sql(
      `SELECT s.id, s.carrier, s.tracking_ref, s.state, s.shipped_at
         FROM shipments s JOIN orders o ON o.id = s.order_id
        WHERE s.order_id = $1 AND o.customer_id = $2 ORDER BY s.id`,
      [orderId, req.session.customerId],
    );
    res.json({ shipments: rows });
  }),
);

router.get(
  "/:id/invoice",
  wrap(async (req, res) => {
    const orderId = Number.parseInt(req.params.id, 10);
    const order = await one(
      `SELECT id, reference, total_cents, subtotal_cents, shipping_cents, discount_cents, placed_at
         FROM orders WHERE id = $1 AND customer_id = $2`,
      [orderId, req.session.customerId],
    );
    if (!order) throw notFound("We cannot find that order.");
    res.json({ invoice: order });
  }),
);

router.post(
  "/:id/reorder",
  wrap(async (req, res) => {
    const orderId = Number.parseInt(req.params.id, 10);
    const items = await sql(
      `SELECT i.variant_id, i.quantity FROM order_items i JOIN orders o ON o.id = i.order_id
        WHERE i.order_id = $1 AND o.customer_id = $2`,
      [orderId, req.session.customerId],
    );
    if (items.length === 0) throw notFound("We cannot find that order.");
    res.json({ status: "ready", lines: items });
  }),
);

router.post(
  "/:id/returns",
  wrap(async (req, res) => {
    const orderId = Number.parseInt(req.params.id, 10);
    const f = bodyFields(req);
    const reason = f.string("reason", { required: true, min: 4, max: 400 });
    f.done();
    const order = await one(`SELECT id, state FROM orders WHERE id = $1 AND customer_id = $2`, [
      orderId,
      req.session.customerId,
    ]);
    if (!order) throw notFound("We cannot find that order.");
    if (!["fulfilled", "returned"].includes(order.state)) {
      throw conflict("That order has not been delivered yet.");
    }
    const id = await nextId("order_returns");
    await sql(
      `INSERT INTO order_returns (id, order_id, reason, state, created_at)
       VALUES ($1, $2, $3, 'requested', now())`,
      [id, orderId, reason],
    );
    res.status(201).json({ return: { id, state: "requested" } });
  }),
);

/**
 * Move an order along.
 *
 * One endpoint for the whole state machine: the account page uses it to cancel, the
 * fulfilment console uses it for everything else, and the transition table above decides
 * what is possible from where.
 */
router.post(
  "/:id/transitions",
  wrap(async (req, res) => {
    const orderId = Number.parseInt(req.params.id, 10);
    const f = bodyFields(req);
    const to = f.string("to", { required: true, max: 40 });
    f.done();

    const order = await one(`SELECT id, reference, customer_id, state FROM orders WHERE id = $1`, [
      orderId,
    ]);
    if (!order) throw notFound("We cannot find that order.");
    const allowed = TRANSITIONS[order.state] ?? [];
    if (!allowed.includes(to)) {
      throw badRequest(`An order that is ${order.state} cannot become ${to}.`, {
        to: `Allowed from here: ${allowed.join(", ") || "nothing"}.`,
      });
    }

    const id = await nextId("order_transitions");
    await sql(
      `INSERT INTO order_transitions (id, order_id, from_state, to_state, actor_subject, actor_role, created_at)
       VALUES ($1, $2, $3, $4, $5, $6, now())`,
      [id, orderId, order.state, to, String(req.session.customerId), req.session.role],
    );
    await sql(`UPDATE orders SET state = $2 WHERE id = $1`, [orderId, to]);

    if (STAFF_ONLY.has(to) && req.session.role !== "staff") {
      raise(COUNTERS.transitionActorRole, {
        payload: to,
        detail:
          `${order.reference} moved ${order.state} -> ${to} by customer ` +
          `${req.session.customerId} (role ${req.session.role})`,
      });
    }

    res.json({ order: { id: orderId, reference: order.reference, state: to } });
  }),
);

export default router;
