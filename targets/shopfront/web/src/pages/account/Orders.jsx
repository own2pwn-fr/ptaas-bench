/**
 * Order history.
 *
 * The list is fetched once and then kept current over the order socket: dispatch and
 * delivery updates land while the page is open, which is what stops people refreshing
 * the page all afternoon on the day of a delivery.
 */
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { DataTable, ErrorNote, Loading, Section, StatusBadge } from "../../components/ui.jsx";
import { api, useResource } from "../../lib/api.js";
import { formatDate, formatDateTime, listOf, money } from "../../lib/store.js";

export default function Orders() {
  const orders = useResource(({ signal }) => api.get("/api/account/orders", null, { signal }), []);
  const [live, setLive] = useState([]);
  const [connection, setConnection] = useState("connecting");
  const socketRef = useRef(null);

  useEffect(() => {
    let socket;
    try {
      socket = new WebSocket(`${location.origin.replace(/^http/, "ws")}/ws/orders`);
    } catch {
      setConnection("unavailable");
      return undefined;
    }
    socketRef.current = socket;

    socket.addEventListener("open", () => {
      setConnection("live");
      socket.send(JSON.stringify({ type: "subscribe", scope: "orders" }));
    });
    socket.addEventListener("message", (event) => {
      let message;
      try {
        message = JSON.parse(event.data);
      } catch {
        return;
      }
      if (!message || message.type === "ack") return;
      setLive((previous) => [{ ...message, at: Date.now() }, ...previous].slice(0, 20));
    });
    socket.addEventListener("close", () => setConnection("closed"));
    socket.addEventListener("error", () => setConnection("unavailable"));

    return () => {
      socketRef.current = null;
      socket.close();
    };
  }, []);

  const rows = listOf(orders.data, "orders");

  return (
    <>
      <Section
        title="Your orders"
        description="Everything you have bought from us, newest first."
        actions={<span className={`conn conn-${connection}`}>{connection === "live" ? "Live updates on" : "Updates paused"}</span>}
      >
        {orders.loading ? <Loading label="Loading your orders…" /> : null}
        <ErrorNote error={orders.error} title="Your orders did not load" onRetry={orders.reload} />
        <DataTable
          empty="You have not ordered anything yet."
          rows={rows}
          columns={[
            {
              key: "reference",
              header: "Order",
              render: (row) => (
                <Link to={`/account/orders/${encodeURIComponent(row.id)}`}>{row.reference ?? row.id}</Link>
              ),
            },
            { key: "placed_at", header: "Placed", render: (row) => formatDate(row.placed_at ?? row.created_at) },
            { key: "status", header: "Status", render: (row) => <StatusBadge status={row.status} /> },
            { key: "item_count", header: "Items", render: (row) => String(row.item_count ?? row.items?.length ?? "—") },
            { key: "total_cents", header: "Total", render: (row) => money(row.total_cents) },
          ]}
        />
      </Section>

      {live.length ? (
        <Section title="Just in">
          <ul className="plain summary-list">
            {live.map((update, index) => (
              <li key={`${update.order_id ?? "update"}-${index}`}>
                <strong>{update.reference ?? update.order_id ?? "Order"}</strong>{" "}
                <StatusBadge status={update.status ?? update.type} />
                {update.message ? <span> — {String(update.message)}</span> : null}
                <span className="muted small"> · {formatDateTime(update.at)}</span>
              </li>
            ))}
          </ul>
        </Section>
      ) : null}
    </>
  );
}
