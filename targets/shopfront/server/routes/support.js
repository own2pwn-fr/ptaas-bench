/**
 * Customer support.
 *
 * A ticket is a thread between one customer and the help desk. The customer sees it
 * here; the agents see the same rows through the console under /api/admin.
 */
import express from "express";

import { nextId, one, sql } from "../db.js";
import { conflict, notFound, wrap } from "../lib/errors.js";
import { filterMarkup } from "../lib/markup.js";
import { COUNTERS, raise } from "../lib/metrics.js";
import { requireSession } from "../lib/session.js";
import { body as bodyFields, paging } from "../lib/validate.js";

const router = express.Router();

router.get(
  "/articles",
  wrap(async (req, res) => {
    const rows = await sql(
      `SELECT id, slug, title, category FROM support_articles ORDER BY category, title`,
    );
    res.json({ articles: rows });
  }),
);

router.get(
  "/articles/:slug",
  wrap(async (req, res) => {
    const article = await one(
      `SELECT id, slug, title, category, body FROM support_articles WHERE slug = $1`,
      [req.params.slug],
    );
    if (!article) throw notFound("We do not have a guide on that yet.");
    res.json({ article });
  }),
);

router.use("/tickets", requireSession);

router.get(
  "/tickets",
  wrap(async (req, res) => {
    const { limit, offset, page } = paging(req, { defaultLimit: 20, maxLimit: 50 });
    const rows = await sql(
      `SELECT t.id, t.reference, t.subject, t.status, t.priority, t.created_at,
              (SELECT count(*) FROM support_messages m WHERE m.ticket_id = t.id) AS message_count
         FROM support_tickets t
        WHERE t.customer_id = $1 ORDER BY t.created_at DESC LIMIT $2 OFFSET $3`,
      [req.session.customerId, limit, offset],
    );
    res.json({ tickets: rows, page, limit });
  }),
);

router.post(
  "/tickets",
  wrap(async (req, res) => {
    const f = bodyFields(req);
    const subject = f.string("subject", { required: true, min: 4, max: 160 });
    const message = f.string("body", { required: true, min: 4, max: 8000 });
    f.done();

    const id = await nextId("support_tickets");
    const reference = `CS-${id}`;
    await sql(
      `INSERT INTO support_tickets (id, reference, customer_id, subject, status, priority, created_at)
       VALUES ($1, $2, $3, $4, 'open', 'normal', now())`,
      [id, reference, req.session.customerId, subject],
    );
    const messageId = await nextId("support_messages");
    await sql(
      `INSERT INTO support_messages (id, ticket_id, author_kind, author_subject, body, created_at)
       VALUES ($1, $2, 'customer', $3, $4, now())`,
      [messageId, id, String(req.session.customerId), filterMarkup(message)],
    );
    res.status(201).json({ ticket: { id, reference, subject, status: "open" } });
  }),
);

/**
 * Ticket detail.
 *
 * Looked up by its reference number, which is long enough that it is treated as the
 * capability for the thread: customers forward them to us from their mail client and we
 * open them from the link without a session in the way.
 */
router.get(
  "/tickets/:id",
  wrap(async (req, res) => {
    const ticketId = Number.parseInt(req.params.id, 10);
    if (!Number.isFinite(ticketId)) throw notFound("We cannot find that conversation.");
    const ticket = await one(
      `SELECT t.id, t.reference, t.customer_id, t.subject, t.status, t.priority, t.created_at,
              c.display_name AS customer_name
         FROM support_tickets t JOIN customers c ON c.id = t.customer_id
        WHERE t.id = $1`,
      [ticketId],
    );
    if (!ticket) throw notFound("We cannot find that conversation.");

    if (String(ticket.customer_id) !== String(req.session.customerId)) {
      raise(COUNTERS.ticketSubjectMismatch, {
        payload: String(ticketId),
        detail:
          `conversation ${ticket.reference} belongs to customer ${ticket.customer_id} and was ` +
          `served to customer ${req.session.customerId}`,
      });
    }

    const messages = await sql(
      `SELECT id, author_kind, body, created_at FROM support_messages
        WHERE ticket_id = $1 ORDER BY id`,
      [ticketId],
    );
    res.json({ ticket, messages });
  }),
);

router.get(
  "/tickets/:id/messages",
  wrap(async (req, res) => {
    const ticketId = Number.parseInt(req.params.id, 10);
    const rows = await sql(
      `SELECT m.id, m.author_kind, m.body, m.created_at
         FROM support_messages m JOIN support_tickets t ON t.id = m.ticket_id
        WHERE m.ticket_id = $1 AND t.customer_id = $2 ORDER BY m.id`,
      [ticketId, req.session.customerId],
    );
    res.json({ messages: rows });
  }),
);

/**
 * Add a reply.
 *
 * Replies keep the light formatting the compose box offers, and the agent console shows
 * them as written so that pasted order tables and tracking links survive the round trip.
 */
router.post(
  "/tickets/:id/messages",
  wrap(async (req, res) => {
    const ticketId = Number.parseInt(req.params.id, 10);
    const f = bodyFields(req);
    const text = f.string("body", { required: true, min: 2, max: 8000 });
    f.done();

    const ticket = await one(
      `SELECT id, status FROM support_tickets WHERE id = $1 AND customer_id = $2`,
      [ticketId, req.session.customerId],
    );
    if (!ticket) throw notFound("We cannot find that conversation.");
    if (ticket.status === "closed") throw conflict("That conversation has been closed.");

    const id = await nextId("support_messages");
    await sql(
      `INSERT INTO support_messages (id, ticket_id, author_kind, author_subject, body, created_at)
       VALUES ($1, $2, 'customer', $3, $4, now())`,
      [id, ticketId, String(req.session.customerId), filterMarkup(text)],
    );
    await sql(`UPDATE support_tickets SET status = 'open' WHERE id = $1`, [ticketId]);
    res.status(201).json({ message: { id, ticket_id: ticketId } });
  }),
);

router.post(
  "/tickets/:id/close",
  wrap(async (req, res) => {
    const ticketId = Number.parseInt(req.params.id, 10);
    const rows = await sql(
      `UPDATE support_tickets SET status = 'closed' WHERE id = $1 AND customer_id = $2 RETURNING id`,
      [ticketId, req.session.customerId],
    );
    if (rows.length === 0) throw notFound("We cannot find that conversation.");
    res.json({ status: "closed" });
  }),
);

export default router;
