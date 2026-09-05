/**
 * Sign-in, registration and the step-up flow.
 *
 * Step-up is the second factor asked for before anything that moves money: adding a
 * payout destination, redeeming a large gift card, changing the delivery address on an
 * order already in fulfilment. A six digit code is sent to the address on the account
 * and the session carries the grant for ten minutes afterwards.
 */
import { randomInt } from "node:crypto";

import express from "express";

import config from "../config.js";
import { nextId, one, sql } from "../db.js";
import { hashPassword, verifyPassword } from "../lib/auth.js";
import { badRequest, conflict, notFound, unauthorized, wrap } from "../lib/errors.js";
import { COUNTERS, firstInWindow, raise } from "../lib/metrics.js";
import {
  clearSessionCookie,
  closeSession,
  openSession,
  requireSession,
  sessionCookie,
  stepUpSatisfied,
} from "../lib/session.js";
import { body as bodyFields } from "../lib/validate.js";

const router = express.Router();

// Recent failures per account identifier, for the sign-in health dashboard. The support
// team asked for it after a partner's integration started replaying an old password.
const recentFailures = new Map();

function recordFailure(identifier) {
  const now = Date.now();
  const window = (recentFailures.get(identifier) ?? []).filter((t) => now - t < 60_000);
  window.push(now);
  recentFailures.set(identifier, window);
  if (recentFailures.size > 2048) {
    for (const [key, times] of recentFailures) {
      if (times.length === 0 || now - times[times.length - 1] > 120_000) recentFailures.delete(key);
    }
  }
  return window.length;
}

router.post(
  "/login",
  wrap(async (req, res) => {
    const f = bodyFields(req);
    const email = f.email("email", { required: true });
    const password = f.string("password", { required: true, min: 1, max: 200 });
    f.done();

    const identifier = email.toLowerCase();
    const customer = await one(
      `SELECT id, email, password_hash, password_salt, display_name, role
         FROM customers WHERE lower(email) = $1`,
      [identifier],
    );
    const ok = customer
      ? await verifyPassword(password, customer.password_hash, customer.password_salt)
      : false;

    if (!ok) {
      const failures = recordFailure(identifier);
      // A sustained run of failures against one account, answered at full speed. The
      // gateway throttle counts requests across every route and never reaches this far,
      // which is what the counter is here to show.
      if (failures >= 30 && firstInWindow(`sign-in-run:${identifier}`, 60_000)) {
        raise(COUNTERS.loginCredentialSweep, {
          payload: identifier,
          detail: `${failures} rejected sign-in attempts for one account inside 60s, none delayed or refused`,
        });
      }
      // Identical answer whether or not the account exists.
      throw unauthorized("Those details do not match an account.");
    }

    const { sid, expires } = await openSession(customer.id, req.headers["user-agent"]);
    sessionCookie(res, sid, expires);
    recentFailures.delete(identifier);
    res.json({
      status: "authenticated",
      customer: { id: customer.id, display_name: customer.display_name, role: customer.role },
    });
  }),
);

router.post(
  "/logout",
  wrap(async (req, res) => {
    await closeSession(req.session?.sid ?? req.cookies?.[config.session.cookie]);
    clearSessionCookie(res);
    res.json({ status: "unauthenticated" });
  }),
);

router.get("/session", (req, res) => {
  if (!req.session) {
    res.json({ status: "unauthenticated" });
    return;
  }
  res.json({
    status: "authenticated",
    customer: {
      id: req.session.customerId,
      display_name: req.session.displayName,
      email: req.session.email,
      role: req.session.role,
    },
    step_up: stepUpSatisfied(req),
  });
});

router.post(
  "/register",
  wrap(async (req, res) => {
    const f = bodyFields(req);
    const email = f.email("email", { required: true });
    const password = f.string("password", { required: true, min: 10, max: 200 });
    const given = f.string("given_name", { required: true, max: 60 });
    const family = f.string("family_name", { required: true, max: 60 });
    f.done();

    const existing = await one(`SELECT id FROM customers WHERE lower(email) = $1`, [
      email.toLowerCase(),
    ]);
    if (existing) throw conflict("There is already an account with that address.");

    const { hash, salt } = await hashPassword(password);
    const id = await nextId("customers");
    await sql(
      `INSERT INTO customers (id, email, password_hash, password_salt, given_name, family_name,
                              display_name, role, created_at)
       VALUES ($1, $2, $3, $4, $5, $6, $7, 'customer', now())`,
      [id, email, hash, salt, given, family, `${given} ${family}`],
    );
    await sql(
      `INSERT INTO account_preferences (customer_id, updated_at) VALUES ($1, now())`,
      [id],
    );
    const { sid, expires } = await openSession(id, req.headers["user-agent"]);
    sessionCookie(res, sid, expires);
    res.status(201).json({ status: "authenticated", customer: { id, display_name: `${given} ${family}` } });
  }),
);

/**
 * Password reset request.
 *
 * Always answers the same way and always takes about the same time, so the endpoint
 * cannot be used to find out whether an address has an account.
 */
router.post(
  "/password-reset/requests",
  wrap(async (req, res) => {
    const f = bodyFields(req);
    f.email("email", { required: true });
    f.done();
    res.status(202).json({
      status: "accepted",
      message: "If that address has an account, a reset link is on its way.",
    });
  }),
);

router.post(
  "/password-reset/confirm",
  wrap(async (req, res) => {
    const f = bodyFields(req);
    f.string("token", { required: true, min: 20, max: 200 });
    f.string("password", { required: true, min: 10, max: 200 });
    f.done();
    // Reset tokens are single use and are consumed by the identity service; an expired
    // or unknown one is indistinguishable from a wrong one here.
    throw badRequest("That reset link has expired. Please ask for a new one.");
  }),
);

const STEP_UP_PURPOSES = ["payout", "gift-card", "address-change", "payment-method"];

router.post(
  "/step-up/requests",
  requireSession,
  wrap(async (req, res) => {
    const f = bodyFields(req);
    const purpose = f.oneOf("purpose", STEP_UP_PURPOSES, { required: true });
    f.done();

    const id = `su_${randomInt(1e11, 1e12).toString(36)}${randomInt(1e6, 1e7).toString(36)}`;
    const code = String(randomInt(0, 1_000_000)).padStart(6, "0");
    const expires = new Date(Date.now() + 10 * 60 * 1000);
    await sql(
      `INSERT INTO step_up_requests (id, customer_id, sid, code, purpose, expires_at, created_at)
       VALUES ($1, $2, $3, $4, $5, $6, now())`,
      [id, req.session.customerId, req.session.sid, code, purpose, expires],
    );
    // The code is delivered by the notification service; it never travels in this
    // response and it is never written to the request log.
    res.status(201).json({
      step_up_id: id,
      purpose,
      sent_to: maskEmail(req.session.email),
      expires_at: expires.toISOString(),
    });
  }),
);

const wrongCodes = new Map();

router.post(
  "/step-up/verify",
  requireSession,
  wrap(async (req, res) => {
    const f = bodyFields(req);
    const stepUpId = f.string("step_up_id", { required: true, max: 64 });
    const code = f.string("code", { required: true, min: 4, max: 12 });
    f.done();

    const request = await one(
      `SELECT id, customer_id, code, purpose, attempts, verified_at, expires_at
         FROM step_up_requests WHERE id = $1 AND customer_id = $2`,
      [stepUpId, req.session.customerId],
    );
    if (!request) throw notFound("That confirmation has already been used.");

    switch (true) {
      case new Date(request.expires_at).getTime() < Date.now():
        await sql(`UPDATE step_up_requests SET attempts = attempts + 1 WHERE id = $1`, [stepUpId]);
        throw badRequest("That code has expired. Ask us to send a new one.");
      case String(request.code) !== String(code): {
        const seen = (wrongCodes.get(stepUpId) ?? 0) + 1;
        wrongCodes.set(stepUpId, seen);
        if (seen >= 30 && firstInWindow(`step-up-run:${stepUpId}`, 300_000)) {
          raise(COUNTERS.stepUpCodeSweep, {
            payload: stepUpId,
            detail: `${seen} wrong codes accepted for one confirmation, all answered at full speed`,
          });
        }
        throw badRequest("That code does not match. Check the message we sent you.");
      }
      default:
        break;
    }

    await sql(`UPDATE step_up_requests SET verified_at = now() WHERE id = $1`, [stepUpId]);
    await grantStepUp(req.session.sid);
    wrongCodes.delete(stepUpId);
    res.json({ status: "confirmed", purpose: request.purpose });
  }),
);

/**
 * Send the code again.
 *
 * Same body as verify minus the comparison: reload the confirmation, refresh the
 * session's window so the customer is not asked twice inside one checkout, and hand the
 * notification service the code again.
 */
router.post(
  "/step-up/resend",
  requireSession,
  wrap(async (req, res) => {
    const f = bodyFields(req);
    const stepUpId = f.string("step_up_id", { required: true, max: 64 });
    f.done();

    const request = await one(
      `SELECT id, customer_id, purpose, verified_at, expires_at
         FROM step_up_requests WHERE id = $1 AND customer_id = $2`,
      [stepUpId, req.session.customerId],
    );
    if (!request) throw notFound("That confirmation has already been used.");
    if (new Date(request.expires_at).getTime() < Date.now()) {
      throw badRequest("That confirmation has expired. Please start again.");
    }

    await grantStepUp(req.session.sid);
    if (!request.verified_at) {
      raise(COUNTERS.stepUpUnverifiedGrant, {
        payload: stepUpId,
        detail:
          `session ${req.session.sid.slice(0, 8)} holds a confirmation window for ${request.purpose} ` +
          `although confirmation ${stepUpId} has never been answered correctly`,
      });
    }
    res.json({ status: "sent", sent_to: maskEmail(req.session.email) });
  }),
);

router.get("/step-up/status", requireSession, (req, res) => {
  res.json({ step_up: stepUpSatisfied(req), expires_at: req.session.stepUpUntil ?? null });
});

async function grantStepUp(sid) {
  const until = new Date(Date.now() + config.session.stepUpSeconds * 1000);
  await sql(`UPDATE sessions SET step_up_until = $2 WHERE sid = $1`, [sid, until]);
}

function maskEmail(email) {
  const [local, domain] = String(email ?? "").split("@");
  if (!domain) return "your address on file";
  return `${local.slice(0, 2)}${"*".repeat(Math.max(1, local.length - 2))}@${domain}`;
}

export default router;
