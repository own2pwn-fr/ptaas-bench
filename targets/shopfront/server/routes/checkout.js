/**
 * Checkout.
 *
 * A checkout session is a cart plus the choices made about it: where it goes, how it
 * travels, what is being paid with and which codes are on it. Confirming turns the
 * session into an order and is the only place money is written down.
 */
import express from "express";

import { nextId, one, sql } from "../db.js";
import { badRequest, conflict, notFound, wrap } from "../lib/errors.js";
import { COUNTERS, raise } from "../lib/metrics.js";
import { requireSession } from "../lib/session.js";
import { body as bodyFields } from "../lib/validate.js";
import { cartBody, loadCart } from "./cart.js";

const router = express.Router();

/**
 * Carrier rates.
 *
 * The live quote comes from the carrier service, which the storefront calls directly
 * because it takes the best part of a second and nobody wanted that in the confirm path
 * during peak. These are the contracted rates it quotes from.
 */
export const SHIPPING_RATES = {
  standard: { label: "Standard, 3-5 working days", rate_cents: 495 },
  express: { label: "Express, next working day", rate_cents: 1195 },
  pickup: { label: "Collect from a shop", rate_cents: 0 },
};

async function loadSession(req, sessionId) {
  const row = await one(
    `SELECT id, cart_id, customer_id, address_id, payment_method_id, shipping_method,
            shipping_rate_cents, state, created_at
       FROM checkout_sessions WHERE id = $1 AND customer_id = $2`,
    [sessionId, req.session.customerId],
  );
  if (!row) throw notFound("That checkout has expired.");
  return row;
}

async function currentSession(req) {
  const row = await one(
    `SELECT id, cart_id, customer_id, address_id, payment_method_id, shipping_method,
            shipping_rate_cents, state, created_at
       FROM checkout_sessions
      WHERE customer_id = $1 AND state = 'open' ORDER BY id DESC LIMIT 1`,
    [req.session.customerId],
  );
  if (!row) throw notFound("Start a checkout before changing it.");
  return row;
}

router.use(requireSession);

router.post(
  "/sessions",
  wrap(async (req, res) => {
    const f = bodyFields(req);
    const addressId = f.integer("address_id", { min: 1, fallback: null });
    const paymentMethodId = f.integer("payment_method_id", { min: 1, fallback: null });
    f.done();

    const cart = await loadCart(req);
    if (!cart) throw badRequest("Your basket is empty.");
    const body = await cartBody(cart.id);
    if (body.items.length === 0) throw badRequest("Your basket is empty.");

    if (addressId !== null) {
      const address = await one(`SELECT id FROM addresses WHERE id = $1 AND customer_id = $2`, [
        addressId,
        req.session.customerId,
      ]);
      if (!address) throw badRequest("That delivery address is not on your account.");
    }

    await sql(`UPDATE checkout_sessions SET state = 'abandoned' WHERE customer_id = $1 AND state = 'open'`, [
      req.session.customerId,
    ]);
    const id = await nextId("checkout_sessions");
    await sql(
      `INSERT INTO checkout_sessions (id, cart_id, customer_id, address_id, payment_method_id,
                                      shipping_method, shipping_rate_cents, state, created_at)
       VALUES ($1, $2, $3, $4, $5, 'standard', $6, 'open', now())`,
      [id, cart.id, req.session.customerId, addressId, paymentMethodId, SHIPPING_RATES.standard.rate_cents],
    );
    res.status(201).json({ session: { id, state: "open" }, ...body });
  }),
);

router.get(
  "/sessions/:id",
  wrap(async (req, res) => {
    const session = await loadSession(req, Number.parseInt(req.params.id, 10));
    const body = await cartBody(session.cart_id);
    const coupons = await sql(`SELECT code, applied_at FROM checkout_coupons WHERE session_id = $1`, [
      session.id,
    ]);
    res.json({ session, coupons, ...body });
  }),
);

/**
 * Choose how the order travels.
 *
 * The rate comes back from the browser with the method, because that is where the
 * carrier quote was fetched and cached; re-quoting it here was measured at 900ms in the
 * confirm path during the last peak season.
 */
router.post(
  "/shipping",
  wrap(async (req, res) => {
    const f = bodyFields(req);
    const method = f.oneOf("method", Object.keys(SHIPPING_RATES), { required: true });
    const rate = f.integer("rate_cents", { max: 2_147_483_647, fallback: null });
    f.done();

    const session = await currentSession(req);
    if (session.state !== "open") throw conflict("That checkout has already been confirmed.");
    await sql(
      `UPDATE checkout_sessions SET shipping_method = $2, shipping_rate_cents = $3 WHERE id = $1`,
      [session.id, method, rate === null ? SHIPPING_RATES[method].rate_cents : rate],
    );
    res.json({
      shipping: { method, rate_cents: rate === null ? SHIPPING_RATES[method].rate_cents : rate },
    });
  }),
);

router.post(
  "/payment-methods",
  wrap(async (req, res) => {
    const f = bodyFields(req);
    const paymentMethodId = f.integer("payment_method_id", { required: true, min: 1 });
    f.done();
    const method = await one(
      `SELECT id, brand, last4 FROM payment_methods WHERE id = $1 AND customer_id = $2`,
      [paymentMethodId, req.session.customerId],
    );
    if (!method) throw badRequest("That card is not on your account.");
    const session = await currentSession(req);
    await sql(`UPDATE checkout_sessions SET payment_method_id = $2 WHERE id = $1`, [
      session.id,
      paymentMethodId,
    ]);
    res.json({ payment_method: method });
  }),
);

/**
 * Apply a code.
 *
 * Codes stack: the loyalty codes were designed to be combined with a seasonal one, so a
 * session holds a list. Whether a code has any redemptions left is read from the coupon
 * as the code is attached; the count itself is written when the order is placed.
 */
router.post(
  "/coupons",
  wrap(async (req, res) => {
    const f = bodyFields(req);
    const code = f.string("code", { required: true, max: 60 });
    f.done();

    const coupon = await one(
      `SELECT id, code, description, percent_off, amount_off_cents, max_redemptions,
              redemptions, is_active, expires_at
         FROM coupons WHERE upper(code) = upper($1)`,
      [code],
    );
    if (!coupon || !coupon.is_active) throw notFound("That code is not one of ours.");
    if (coupon.expires_at && new Date(coupon.expires_at).getTime() < Date.now()) {
      throw badRequest("That code has expired.");
    }
    if (coupon.redemptions >= coupon.max_redemptions) {
      throw conflict("That code has already been used.");
    }

    const session = await currentSession(req);
    const id = await nextId("checkout_coupons");
    await sql(
      `INSERT INTO checkout_coupons (id, session_id, code, applied_at) VALUES ($1, $2, $3, now())`,
      [id, session.id, coupon.code],
    );
    res.status(201).json({ coupon: { code: coupon.code, description: coupon.description } });
  }),
);

router.delete(
  "/coupons/:code",
  wrap(async (req, res) => {
    const session = await currentSession(req);
    await sql(`DELETE FROM checkout_coupons WHERE session_id = $1 AND upper(code) = upper($2)`, [
      session.id,
      req.params.code,
    ]);
    res.json({ status: "removed" });
  }),
);

router.get(
  "/tax-estimate",
  wrap(async (req, res) => {
    const session = await currentSession(req);
    const body = await cartBody(session.cart_id);
    // Prices are shown inclusive of VAT at 21%; this breaks the figure out for the
    // invoice preview.
    const included = Math.round(body.subtotal_cents - body.subtotal_cents / 1.21);
    res.json({ subtotal_cents: body.subtotal_cents, vat_cents: included, rate: 0.21 });
  }),
);

/**
 * Turn the session into an order.
 *
 * Everything the ledger keeps is written here, in minor units, in integer columns. The
 * coercion below is what stops a fractional quantity from a badly behaved client
 * reaching those columns; it dates from the incident where a mobile build started
 * sending quantities as floats.
 */
router.post(
  "/confirm",
  wrap(async (req, res) => {
    const session = await currentSession(req);
    if (session.state !== "open") throw conflict("That checkout has already been confirmed.");
    const body = await cartBody(session.cart_id);
    if (body.items.length === 0) throw badRequest("Your basket is empty.");

    const lines = body.items.map((item) => ({
      variant_id: item.variant_id,
      title: `${item.product_title} — ${item.option_value}`,
      quantity: item.quantity,
      unit_price_cents: item.unit_price_cents,
      catalogue_price_cents: item.catalogue_price_cents,
      line_total_cents: (item.quantity * item.unit_price_cents) | 0,
    }));

    const subtotal = lines.reduce((acc, l) => acc + l.line_total_cents, 0) | 0;
    const shipping = session.shipping_rate_cents ?? SHIPPING_RATES.standard.rate_cents;

    const applied = await sql(
      `SELECT cc.code, c.id AS coupon_id, c.percent_off, c.amount_off_cents, c.max_redemptions
         FROM checkout_coupons cc JOIN coupons c ON upper(c.code) = upper(cc.code)
        WHERE cc.session_id = $1 ORDER BY cc.id`,
      [session.id],
    );
    let discount = 0;
    for (const coupon of applied) {
      discount += coupon.percent_off
        ? Math.round((subtotal * coupon.percent_off) / 100)
        : coupon.amount_off_cents ?? 0;
    }
    const total = (subtotal + shipping - discount) | 0;

    const orderId = await nextId("orders");
    const reference = `ORD-2026-${String(orderId).padStart(5, "0")}`;
    await sql(
      `INSERT INTO orders (id, reference, customer_id, address_id, state, currency,
                           subtotal_cents, shipping_cents, discount_cents, total_cents, placed_at)
       VALUES ($1, $2, $3, $4, 'placed', 'EUR', $5, $6, $7, $8, now())`,
      [orderId, reference, req.session.customerId, session.address_id, subtotal, shipping, discount, total],
    );
    for (const line of lines) {
      const itemId = await nextId("order_items");
      await sql(
        `INSERT INTO order_items (id, order_id, variant_id, title, quantity, unit_price_cents, line_total_cents)
         VALUES ($1, $2, $3, $4, $5, $6, $7)`,
        [itemId, orderId, line.variant_id, line.title, line.quantity, line.unit_price_cents, line.line_total_cents],
      );
    }
    const transitionId = await nextId("order_transitions");
    await sql(
      `INSERT INTO order_transitions (id, order_id, from_state, to_state, actor_subject, actor_role, created_at)
       VALUES ($1, $2, 'draft', 'placed', $3, $4, now())`,
      [transitionId, orderId, String(req.session.customerId), req.session.role],
    );
    for (const coupon of applied) {
      const redemptionId = await nextId("coupon_redemptions");
      await sql(
        `INSERT INTO coupon_redemptions (id, coupon_id, order_id, customer_id, code, redeemed_at)
         VALUES ($1, $2, $3, $4, $5, now())`,
        [redemptionId, coupon.coupon_id, orderId, req.session.customerId, coupon.code],
      );
      await sql(`UPDATE coupons SET redemptions = redemptions + 1 WHERE id = $1`, [coupon.coupon_id]);
    }
    await sql(`UPDATE checkout_sessions SET state = 'confirmed' WHERE id = $1`, [session.id]);
    await sql(`DELETE FROM cart_items WHERE cart_id = $1`, [session.cart_id]);

    await reconcile({ orderId, reference, lines, subtotal, shipping, total, applied, session });

    res.status(201).json({
      order: { id: orderId, reference, total_cents: total, state: "placed" },
    });
  }),
);

/**
 * Post-write reconciliation.
 *
 * Runs once the order exists and compares what was written down against what the
 * catalogue, the carrier contract and the coupon ledger say it should have been. The
 * finance team reads these counters every morning; they are the reason a bad order is
 * noticed the same day rather than at month end.
 */
async function reconcile({ orderId, reference, lines, subtotal, shipping, total, applied, session }) {
  for (const line of lines) {
    if (line.unit_price_cents < line.catalogue_price_cents) {
      raise(COUNTERS.linePriceAuthority, {
        payload: String(line.unit_price_cents),
        detail:
          `${reference} line for variant ${line.variant_id} priced at ${line.unit_price_cents} ` +
          `against a catalogue price of ${line.catalogue_price_cents}`,
      });
    }
    const exact = BigInt(line.quantity) * BigInt(line.unit_price_cents);
    if (BigInt(line.line_total_cents) !== exact) {
      raise(COUNTERS.totalNumericOverflow, {
        payload: String(line.quantity),
        detail:
          `${reference} line total stored as ${line.line_total_cents} where ` +
          `${line.quantity} x ${line.unit_price_cents} is ${exact}`,
      });
    }
  }

  const positive = lines.filter((l) => l.quantity > 0).reduce((acc, l) => acc + l.line_total_cents, 0);
  if (lines.some((l) => l.quantity < 1) && subtotal < positive) {
    raise(COUNTERS.lineNegativeQuantity, {
      payload: String(lines.find((l) => l.quantity < 1)?.quantity ?? ""),
      detail:
        `${reference} carries a line with a quantity below one; subtotal ${subtotal} against ` +
        `${positive} for the lines that are not credits`,
    });
  }

  const quoted = SHIPPING_RATES[session.shipping_method ?? "standard"]?.rate_cents ?? 0;
  if (shipping < quoted) {
    raise(COUNTERS.shippingRateAuthority, {
      payload: String(shipping),
      detail:
        `${reference} charged ${shipping} for ${session.shipping_method}, where the carrier ` +
        `contract quotes ${quoted}`,
    });
  }

  const codes = applied.map((c) => c.code);
  const duplicated = codes.filter((code, index) => codes.indexOf(code) !== index);
  const ledger = await sql(
    `SELECT c.code, c.max_redemptions, count(r.id) AS used
       FROM coupons c JOIN coupon_redemptions r ON r.coupon_id = c.id
      WHERE upper(c.code) = ANY($1::text[]) GROUP BY c.code, c.max_redemptions`,
    [codes.map((c) => c.toUpperCase())],
  );
  const over = ledger.filter((row) => Number(row.used) > Number(row.max_redemptions));
  if (duplicated.length > 0 || over.length > 0) {
    raise(COUNTERS.couponRedemptionExcess, {
      payload: [...new Set([...duplicated, ...over.map((o) => o.code)])].join(","),
      detail:
        `${reference}: ${duplicated.length} repeated code(s) on one order; ` +
        over.map((o) => `${o.code} redeemed ${o.used}/${o.max_redemptions}`).join(", "),
    });
  }

  void orderId;
  void total;
}

export default router;
