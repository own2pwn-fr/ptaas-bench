import { Link } from "react-router-dom";

import { ErrorNote, Loading, Section, StatusBadge } from "../../components/ui.jsx";
import { api, useResource } from "../../lib/api.js";
import { formatDateTime, listOf, money } from "../../lib/store.js";

export default function AdminHome() {
  const orders = useResource(({ signal }) => api.get("/api/admin/orders", { limit: 5 }, { signal }), []);
  const tickets = useResource(({ signal }) => api.get("/api/admin/support/tickets", { limit: 5 }, { signal }), []);
  const imports = useResource(({ signal }) => api.get("/api/admin/imports", null, { signal }), []);
  const status = useResource(({ signal }) => api.get("/api/status", null, { signal }), []);

  const orderRows = listOf(orders.data, "orders");
  const ticketRows = listOf(tickets.data, "tickets");
  const importRows = listOf(imports.data, "imports");

  return (
    <>
      <Section title="Today" description="What needs a person before the vans go out at four.">
        <ErrorNote error={status.error} title="The service state did not load" onRetry={status.reload} />
        <div className="grid three">
          <article className="card">
            <p className="stat">{orderRows.length}</p>
            <p className="muted small">recent orders</p>
          </article>
          <article className="card">
            <p className="stat">{ticketRows.filter((t) => t.status !== "closed").length}</p>
            <p className="muted small">open requests</p>
          </article>
          <article className="card">
            <p className="stat">{importRows.filter((row) => row.status === "running").length}</p>
            <p className="muted small">imports running</p>
          </article>
        </div>
      </Section>

      <Section title="Latest orders" actions={<Link className="btn btn-quiet" to="/admin/orders">All orders</Link>}>
        {orders.loading ? <Loading rows={2} /> : null}
        <ErrorNote error={orders.error} title="Orders did not load" onRetry={orders.reload} />
        <ul className="plain summary-list">
          {orderRows.map((order) => (
            <li key={order.id}>
              <strong>{order.reference ?? order.id}</strong>{" "}
              <StatusBadge status={order.status} />
              <span className="muted small"> · {order.customer_email ?? order.email ?? ""}</span>
              <span className="right">{money(order.total_cents)}</span>
            </li>
          ))}
          {!orders.loading && orderRows.length === 0 ? <li className="muted">Nothing yet today.</li> : null}
        </ul>
      </Section>

      <Section title="Waiting on us" actions={<Link className="btn btn-quiet" to="/admin/support">Customer service</Link>}>
        {tickets.loading ? <Loading rows={2} /> : null}
        <ErrorNote error={tickets.error} title="Requests did not load" onRetry={tickets.reload} />
        <ul className="plain summary-list">
          {ticketRows.map((ticket) => (
            <li key={ticket.id}>
              <strong>{ticket.subject}</strong> <StatusBadge status={ticket.status} />
              <span className="muted small"> · {formatDateTime(ticket.updated_at ?? ticket.created_at)}</span>
            </li>
          ))}
          {!tickets.loading && ticketRows.length === 0 ? <li className="muted">Inbox clear.</li> : null}
        </ul>
      </Section>
    </>
  );
}
