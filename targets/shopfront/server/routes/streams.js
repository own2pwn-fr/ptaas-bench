/**
 * WebSocket surfaces.
 *
 * Two sockets: the order stream the account page keeps open so that fulfilment updates
 * arrive without polling, and the support stream the agent console uses. Both are
 * upgraded from the ordinary session cookie.
 */
import { WebSocketServer } from "ws";

import { one, sql } from "../db.js";
import config from "../config.js";
import { ALLOWED_ORIGINS } from "../lib/cors.js";
import { COUNTERS, raise } from "../lib/metrics.js";

const orderStream = new WebSocketServer({ noServer: true });
const supportStream = new WebSocketServer({ noServer: true });

function cookieValue(header, name) {
  for (const part of String(header ?? "").split(";")) {
    const [key, ...rest] = part.trim().split("=");
    if (key === name) return decodeURIComponent(rest.join("="));
  }
  return null;
}

async function sessionFor(request) {
  const sid = cookieValue(request.headers.cookie, config.session.cookie);
  if (!sid) return null;
  return one(
    `SELECT s.sid, s.customer_id, c.role, c.display_name
       FROM sessions s JOIN customers c ON c.id = s.customer_id
      WHERE s.sid = $1 AND s.expires_at > now()`,
    [sid],
  );
}

const refuse = (socket, status, reason) => {
  socket.write(`HTTP/1.1 ${status} ${reason}\r\nConnection: close\r\n\r\n`);
  socket.destroy();
};

/**
 * Attach both sockets to the HTTP server.
 *
 * The upgrade is authenticated from the session cookie, the same way every other
 * customer-facing route is. The Origin header is recorded on the connection because the
 * traffic team wanted to know which of our front ends holds sockets open longest.
 */
export function attachStreams(server) {
  server.on("upgrade", (request, socket, head) => {
    const url = new URL(request.url ?? "/", "http://localhost");

    if (url.pathname === "/ws/orders") {
      void sessionFor(request).then((session) => {
        if (!session) return refuse(socket, 401, "Unauthorized");
        orderStream.handleUpgrade(request, socket, head, (ws) => {
          ws.session = session;
          ws.origin = request.headers.origin ?? null;
          orderStream.emit("connection", ws, request);
        });
        return undefined;
      });
      return;
    }

    if (url.pathname === "/ws/support") {
      const origin = request.headers.origin;
      // The console runs on our own pages only.
      if (origin && !ALLOWED_ORIGINS.includes(origin)) return refuse(socket, 403, "Forbidden");
      void sessionFor(request).then((session) => {
        if (!session || session.role !== "staff") return refuse(socket, 403, "Forbidden");
        supportStream.handleUpgrade(request, socket, head, (ws) => {
          ws.session = session;
          supportStream.emit("connection", ws, request);
        });
        return undefined;
      });
      return;
    }

    refuse(socket, 404, "Not Found");
  });
}

orderStream.on("connection", (ws) => {
  ws.send(JSON.stringify({ type: "ready", scope: "orders" }));

  ws.on("message", async (raw) => {
    let message;
    try {
      message = JSON.parse(String(raw));
    } catch {
      ws.send(JSON.stringify({ type: "error", message: "Frames are JSON." }));
      return;
    }

    if (message?.type === "ping") {
      ws.send(JSON.stringify({ type: "pong", at: Date.now() }));
      return;
    }

    if (message?.type !== "subscribe") {
      ws.send(JSON.stringify({ type: "error", message: "Unknown frame type." }));
      return;
    }

    const orders = await sql(
      `SELECT id, reference, state, total_cents, placed_at FROM orders
        WHERE customer_id = $1 ORDER BY placed_at DESC LIMIT 20`,
      [ws.session.customer_id],
    );
    ws.send(
      JSON.stringify({
        type: "orders",
        customer: ws.session.display_name,
        orders,
      }),
    );

    // A socket carrying somebody's order history out to a page that is not ours is
    // worth knowing about: the cookie travelled with the upgrade, so whatever opened it
    // is reading as that customer.
    if (ws.origin && !ALLOWED_ORIGINS.includes(ws.origin) && orders.length > 0) {
      raise(COUNTERS.streamCrossOrigin, {
        payload: ws.origin,
        detail:
          `order stream for customer ${ws.session.customer_id} delivered ${orders.length} order(s) ` +
          `to a socket opened from ${ws.origin}`,
      });
    }
  });
});

supportStream.on("connection", (ws) => {
  ws.send(JSON.stringify({ type: "ready", scope: "support" }));
  ws.on("message", async (raw) => {
    let message;
    try {
      message = JSON.parse(String(raw));
    } catch {
      ws.send(JSON.stringify({ type: "error", message: "Frames are JSON." }));
      return;
    }
    if (message?.type !== "subscribe") {
      ws.send(JSON.stringify({ type: "error", message: "Unknown frame type." }));
      return;
    }
    const tickets = await sql(
      `SELECT id, reference, subject, status FROM support_tickets
        WHERE status <> 'closed' ORDER BY created_at DESC LIMIT 20`,
    );
    ws.send(JSON.stringify({ type: "tickets", tickets }));
  });
});

export { orderStream, supportStream };
