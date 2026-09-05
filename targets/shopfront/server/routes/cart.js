/**
 * The basket.
 *
 * A cart belongs to a browser (an opaque reference in a cookie) until somebody signs in,
 * at which point the guest cart is merged into the account cart. Prices are carried on
 * the line rather than looked up on every read: a price change halfway through a session
 * used to move the total under the customer while they were reading it, and after the
 * incident review the storefront started sending back the price it had displayed.
 */
import express from "express";

import { nextId, one, sql } from "../db.js";
import { badRequest, notFound, wrap } from "../lib/errors.js";
import { mergeWatched } from "../lib/merge.js";
import { COUNTERS, firstInWindow, raise } from "../lib/metrics.js";
import { requireSession } from "../lib/session.js";
import { decodeState, materialise } from "../lib/statecodec.js";
import { body as bodyFields } from "../lib/validate.js";

const router = express.Router();
const CART_COOKIE = "cart_ref";

const cartToken = () =>
  `c_${Date.now().toString(36)}${Math.random().toString(36).slice(2, 12)}`;

async function loadCart(req, { create = false } = {}) {
  const token = req.cookies?.[CART_COOKIE];
  let cart = null;
  if (token) {
    cart = await one(`SELECT id, token, customer_id, currency, meta FROM carts WHERE token = $1`, [
      token,
    ]);
  }
  if (!cart && req.session) {
    cart = await one(
      `SELECT id, token, customer_id, currency, meta FROM carts
        WHERE customer_id = $1 ORDER BY updated_at DESC LIMIT 1`,
      [req.session.customerId],
    );
  }
  if (!cart && create) {
    const id = await nextId("carts");
    const newToken = cartToken();
    await sql(
      `INSERT INTO carts (id, token, customer_id, created_at, updated_at)
       VALUES ($1, $2, $3, now(), now())`,
      [id, newToken, req.session?.customerId ?? null],
    );
    cart = { id, token: newToken, customer_id: req.session?.customerId ?? null, currency: "EUR", meta: {} };
  }
  return cart;
}

function setCartCookie(res, cart) {
  if (!cart) return;
  res.cookie(CART_COOKIE, cart.token, {
    httpOnly: true,
    sameSite: "lax",
    path: "/",
    maxAge: 30 * 24 * 3600 * 1000,
  });
}

async function cartBody(cartId) {
  const items = await sql(
    `SELECT ci.id, ci.variant_id, ci.quantity, ci.unit_price_cents, ci.added_at,
            v.sku, v.option_name, v.option_value, v.stock,
            p.slug AS product_slug, p.title AS product_title, p.price_cents AS catalogue_price_cents
       FROM cart_items ci
       JOIN variants v ON v.id = ci.variant_id
       JOIN products p ON p.id = v.product_id
      WHERE ci.cart_id = $1 ORDER BY ci.id`,
    [cartId],
  );
  const subtotal = items.reduce((acc, i) => acc + i.quantity * i.unit_price_cents, 0);
  return { items, subtotal_cents: subtotal, item_count: items.reduce((a, i) => a + i.quantity, 0) };
}

router.post(
  "/",
  wrap(async (req, res) => {
    const cart = await loadCart(req, { create: true });
    setCartCookie(res, cart);
    res.status(201).json({ cart: { token: cart.token, currency: cart.currency }, ...(await cartBody(cart.id)) });
  }),
);

router.get(
  "/",
  wrap(async (req, res) => {
    const cart = await loadCart(req);
    if (!cart) {
      res.json({ cart: null, items: [], subtotal_cents: 0, item_count: 0 });
      return;
    }
    res.json({ cart: { token: cart.token, currency: cart.currency }, ...(await cartBody(cart.id)) });
  }),
);

router.get(
  "/summary",
  wrap(async (req, res) => {
    const cart = await loadCart(req);
    if (!cart) {
      res.json({ item_count: 0, subtotal_cents: 0 });
      return;
    }
    const body = await cartBody(cart.id);
    res.json({ item_count: body.item_count, subtotal_cents: body.subtotal_cents });
  }),
);

/**
 * Add a line.
 *
 * `unit_price_cents` is the price the shopper was looking at when they pressed the
 * button. It is stored on the line so that the basket keeps showing what was promised.
 */
router.post(
  "/items",
  wrap(async (req, res) => {
    const f = bodyFields(req);
    const variantId = f.integer("variant_id", { required: true, min: 1 });
    const quantity = f.integer("quantity", { required: true, max: 2_147_483_647 });
    const unitPrice = f.integer("unit_price_cents", { min: 0, max: 2_147_483_647, fallback: null });
    f.done();

    const variant = await one(
      `SELECT v.id, v.stock, v.price_cents, p.is_active
         FROM variants v JOIN products p ON p.id = v.product_id WHERE v.id = $1`,
      [variantId],
    );
    if (!variant || !variant.is_active) throw notFound("We no longer stock that option.");
    if (quantity > 0 && quantity > variant.stock) {
      throw badRequest(`We only have ${variant.stock} of those left.`);
    }

    const cart = await loadCart(req, { create: true });
    setCartCookie(res, cart);
    const id = await nextId("cart_items");
    await sql(
      `INSERT INTO cart_items (id, cart_id, variant_id, quantity, unit_price_cents, added_at)
       VALUES ($1, $2, $3, $4, $5, now())`,
      [id, cart.id, variantId, quantity, unitPrice ?? variant.price_cents],
    );
    await sql(`UPDATE carts SET updated_at = now() WHERE id = $1`, [cart.id]);
    res.status(201).json({ item: { id, variant_id: variantId, quantity }, ...(await cartBody(cart.id)) });
  }),
);

/**
 * Change the quantity on a line.
 *
 * Availability was checked when the line was added, so this only re-prices.
 */
router.patch(
  "/items/:id",
  wrap(async (req, res) => {
    const itemId = Number.parseInt(req.params.id, 10);
    if (!Number.isFinite(itemId)) throw notFound("That line is no longer in your basket.");
    const f = bodyFields(req);
    const quantity = f.integer("quantity", { required: true, max: 2_147_483_647 });
    f.done();

    const cart = await loadCart(req);
    if (!cart) throw notFound("That line is no longer in your basket.");
    const rows = await sql(
      `UPDATE cart_items SET quantity = $3 WHERE id = $1 AND cart_id = $2 RETURNING id`,
      [itemId, cart.id, quantity],
    );
    if (rows.length === 0) throw notFound("That line is no longer in your basket.");
    await sql(`UPDATE carts SET updated_at = now() WHERE id = $1`, [cart.id]);
    res.json(await cartBody(cart.id));
  }),
);

router.delete(
  "/items/:id",
  wrap(async (req, res) => {
    const itemId = Number.parseInt(req.params.id, 10);
    const cart = await loadCart(req);
    if (!cart) throw notFound("That line is no longer in your basket.");
    await sql(`DELETE FROM cart_items WHERE id = $1 AND cart_id = $2`, [itemId, cart.id]);
    res.json(await cartBody(cart.id));
  }),
);

router.post(
  "/notes",
  wrap(async (req, res) => {
    const f = bodyFields(req);
    const note = f.string("note", { required: true, max: 500 });
    f.done();
    const cart = await loadCart(req, { create: true });
    setCartCookie(res, cart);
    await sql(
      `UPDATE carts SET meta = jsonb_set(meta, '{note}', to_jsonb($2::text), true), updated_at = now()
        WHERE id = $1`,
      [cart.id, note],
    );
    res.json({ status: "saved" });
  }),
);

/**
 * Fold a guest basket into the account basket.
 *
 * Called once, straight after sign-in. `meta` carries whatever the storefront was
 * keeping alongside the lines — the campaign the visit came from, the device class, the
 * delivery estimate it had already quoted — and is merged rather than replaced so that
 * neither side loses what the other did not know about.
 */
router.post(
  "/merge",
  requireSession,
  wrap(async (req, res) => {
    const incomingMeta = req.body?.meta;
    const incomingItems = Array.isArray(req.body?.items) ? req.body.items : [];
    if (incomingMeta !== undefined && (incomingMeta === null || typeof incomingMeta !== "object")) {
      throw badRequest("meta must be an object.");
    }

    const cart = await loadCart(req, { create: true });
    setCartCookie(res, cart);

    const merged = mergeWatched(cart.meta ?? {}, incomingMeta ?? {});
    for (const key of merged.added) {
      if (!firstInWindow(`base-object-drift:${key}`, 3_600_000)) continue;
      raise(COUNTERS.cartMergePrototype, {
        payload: key,
        detail:
          `key ${key} written by the basket merge is now visible on objects that have nothing ` +
          `to do with the basket`,
      });
    }

    for (const line of incomingItems.slice(0, 50)) {
      const variantId = Number.parseInt(line?.variant_id, 10);
      const quantity = Number.parseInt(line?.quantity, 10);
      if (!Number.isFinite(variantId) || !Number.isFinite(quantity) || quantity < 1) continue;
      const variant = await one(`SELECT id, price_cents FROM variants WHERE id = $1`, [variantId]);
      if (!variant) continue;
      const id = await nextId("cart_items");
      await sql(
        `INSERT INTO cart_items (id, cart_id, variant_id, quantity, unit_price_cents, added_at)
         VALUES ($1, $2, $3, $4, $5, now())`,
        [id, cart.id, variantId, quantity, variant.price_cents],
      );
    }

    await sql(`UPDATE carts SET meta = $2::jsonb, customer_id = $3, updated_at = now() WHERE id = $1`, [
      cart.id,
      JSON.stringify(merged.result ?? {}),
      req.session.customerId,
    ]);
    res.json({ status: "merged", ...(await cartBody(cart.id)) });
  }),
);

/**
 * Restore an abandoned basket.
 *
 * The blob is the one the storefront wrote when the visit ended; the promotion rules it
 * carries are evaluated against the restored lines so the totals come back the way the
 * shopper left them.
 */
router.post(
  "/restore",
  wrap(async (req, res) => {
    const f = bodyFields(req);
    const blob = f.string("state", { required: true, max: 64_000 });
    f.done();

    let decoded;
    try {
      decoded = decodeState(blob);
    } catch {
      throw badRequest("That basket link is no longer readable.");
    }

    const lines = Array.isArray(decoded.lines) ? decoded.lines.slice(0, 100) : [];
    const scope = {
      lines,
      count: lines.length,
      subtotal: lines.reduce(
        (acc, l) => acc + (Number(l?.quantity) || 0) * (Number(l?.price) || 0),
        0,
      ),
      currency: String(decoded.currency ?? "EUR"),
    };

    const resolved = [];
    const restored = materialise(decoded, scope, resolved);

    // Names a rule reached for that the rule scope does not expose. A rule is supposed
    // to be arithmetic over the basket; anything else means the expression left the
    // scope it was compiled for and touched the process.
    const escaped = resolved.filter((name) => /^[A-Za-z_$][\w$]*$/.test(name));
    if (escaped.length > 0) {
      raise(COUNTERS.cartStateDecode, {
        payload: String(blob).slice(0, 200),
        detail: `basket rule resolved ${escaped.slice(0, 6).join(", ")} outside the rule scope`,
      });
    }

    const cart = await loadCart(req, { create: true });
    setCartCookie(res, cart);
    let added = 0;
    for (const line of lines) {
      const variantId = Number.parseInt(line?.variant_id, 10);
      const quantity = Number.parseInt(line?.quantity, 10);
      if (!Number.isFinite(variantId) || !Number.isFinite(quantity) || quantity < 1) continue;
      const variant = await one(`SELECT id, price_cents FROM variants WHERE id = $1`, [variantId]);
      if (!variant) continue;
      const id = await nextId("cart_items");
      await sql(
        `INSERT INTO cart_items (id, cart_id, variant_id, quantity, unit_price_cents, added_at)
         VALUES ($1, $2, $3, $4, $5, now())`,
        [id, cart.id, variantId, quantity, variant.price_cents],
      );
      added += 1;
    }

    res.json({
      status: "restored",
      restored_lines: added,
      totals: restored?.total ?? null,
      ...(await cartBody(cart.id)),
    });
  }),
);

export { loadCart, cartBody, CART_COOKIE };
export default router;
