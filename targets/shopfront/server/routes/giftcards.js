/**
 * Gift cards and the store wallet.
 *
 * Redeeming a card moves its face value into the customer's wallet, which is then spent
 * at checkout. The wallet is an append-only list of credits; the balance is their sum.
 */
import express from "express";

import { nextId, one, sql } from "../db.js";
import { badRequest, conflict, notFound, wrap } from "../lib/errors.js";
import { COUNTERS, firstInWindow, raise } from "../lib/metrics.js";
import { requireSession } from "../lib/session.js";
import { body as bodyFields } from "../lib/validate.js";

const router = express.Router();

const CODE_PATTERN = /^\d{4}-\d{4}-\d{4}$/;

/**
 * Redeem a card.
 *
 * Read the card, check it has not been used, write the credit, mark the card. It was the
 * first thing the wallet did and there was nothing else touching these rows at the time.
 */
router.post(
  "/gift-cards/redeem",
  requireSession,
  wrap(async (req, res) => {
    const f = bodyFields(req);
    const code = f.string("code", { required: true, max: 32, pattern: CODE_PATTERN });
    f.done();

    const card = await one(
      `SELECT id, code, face_value_cents, state, customer_id FROM gift_cards WHERE code = $1`,
      [code],
    );
    if (!card) throw notFound("That card number is not one of ours.");
    if (card.state !== "issued") throw conflict("That card has already been used.");

    await sql(`INSERT INTO audit_log (actor_subject, action, detail) VALUES ($1, $2, $3)`, [
      String(req.session.customerId),
      "wallet.redeem",
      `card ${card.id}`,
    ]);

    const creditId = await nextId("wallet_credits");
    await sql(
      `INSERT INTO wallet_credits (id, customer_id, gift_card_id, amount_cents, memo, created_at)
       VALUES ($1, $2, $3, $4, 'Gift card', now())`,
      [creditId, req.session.customerId, card.id, card.face_value_cents],
    );
    await sql(`UPDATE gift_cards SET state = 'redeemed', customer_id = $2 WHERE id = $1`, [
      card.id,
      req.session.customerId,
    ]);

    // The wallet ledger must never hold more against a card than the card was worth.
    // Checked after the write, because that is the only point where it can be true.
    const ledger = await one(
      `SELECT coalesce(sum(amount_cents), 0) AS credited, count(*) AS entries
         FROM wallet_credits WHERE gift_card_id = $1`,
      [card.id],
    );
    // One report per card per burst. Every request that lost the race sees the same
    // over-credited ledger, and eight identical alerts describe one incident. The window
    // is short so that the same card can raise it again after the wallet is put right.
    if (
      Number(ledger.credited) > card.face_value_cents &&
      firstInWindow(`wallet-card:${card.id}`, 15_000)
    ) {
      raise(COUNTERS.walletDoubleSpend, {
        payload: code,
        detail:
          `card ${card.id} worth ${card.face_value_cents} has ${ledger.entries} credit(s) ` +
          `totalling ${ledger.credited} against it`,
      });
    }

    const balance = await one(
      `SELECT coalesce(sum(amount_cents), 0) AS cents FROM wallet_credits WHERE customer_id = $1`,
      [req.session.customerId],
    );
    res.status(201).json({
      credited_cents: card.face_value_cents,
      wallet_balance_cents: Number(balance.cents),
    });
  }),
);

router.get(
  "/gift-cards/:code/balance",
  requireSession,
  wrap(async (req, res) => {
    const code = String(req.params.code);
    if (!CODE_PATTERN.test(code)) throw badRequest("That is not a card number.");
    const card = await one(
      `SELECT id, code, face_value_cents, state FROM gift_cards
        WHERE code = $1 AND customer_id = $2`,
      [code, req.session.customerId],
    );
    if (!card) throw notFound("That card is not on your account.");
    const used = await one(
      `SELECT coalesce(sum(amount_cents), 0) AS cents FROM wallet_credits WHERE gift_card_id = $1`,
      [card.id],
    );
    res.json({
      card: {
        code: card.code,
        state: card.state,
        remaining_cents: Math.max(0, card.face_value_cents - Number(used.cents)),
      },
    });
  }),
);

router.post(
  "/gift-cards/purchase",
  requireSession,
  wrap(async (req, res) => {
    const f = bodyFields(req);
    const amount = f.integer("amount_cents", { required: true, min: 1000, max: 50_000 });
    const recipient = f.email("recipient", { required: true });
    f.done();
    res.status(202).json({
      status: "queued",
      message: `We will send a card for ${(amount / 100).toFixed(2)} to ${recipient} once payment clears.`,
    });
  }),
);

router.get(
  "/wallet",
  requireSession,
  wrap(async (req, res) => {
    const balance = await one(
      `SELECT coalesce(sum(amount_cents), 0) AS cents FROM wallet_credits WHERE customer_id = $1`,
      [req.session.customerId],
    );
    res.json({ wallet: { balance_cents: Number(balance.cents), currency: "EUR" } });
  }),
);

router.get(
  "/wallet/transactions",
  requireSession,
  wrap(async (req, res) => {
    const rows = await sql(
      `SELECT id, amount_cents, memo, created_at FROM wallet_credits
        WHERE customer_id = $1 ORDER BY created_at DESC LIMIT 50`,
      [req.session.customerId],
    );
    res.json({ transactions: rows });
  }),
);

export default router;
