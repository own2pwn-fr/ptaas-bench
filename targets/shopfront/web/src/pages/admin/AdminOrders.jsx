import { useState } from "react";

import { DataTable, ErrorNote, Loading, Notice, Section, StatusBadge } from "../../components/ui.jsx";
import { api, useAction, useResource } from "../../lib/api.js";
import { formatDateTime, humanise, listOf, money } from "../../lib/store.js";

const NEXT_STATES = ["paid", "picking", "fulfilled", "refunded", "cancelled"];

export default function AdminOrders() {
  const [status, setStatus] = useState("");
  const [query, setQuery] = useState("");
  const orders = useResource(
    ({ signal }) => api.get("/api/admin/orders", { status, q: query, limit: 50 }, { signal }),
    [status, query],
  );
  const action = useAction();
  const [notice, setNotice] = useState(null);

  const move = (order, to) =>
    action.run(async () => {
      await api.post(`/api/orders/${encodeURIComponent(order.id)}/transitions`, { to });
      setNotice(`${order.reference ?? order.id} → ${humanise(to)}`);
      orders.reload();
    });

  return (
    <Section title="Orders" description="Everything the shop has taken, across all customers.">
      <div className="row gap filters">
        <label className="inline-field">
          <span className="muted small">Status</span>
          <select className="input" value={status} onChange={(event) => setStatus(event.target.value)}>
            <option value="">Any</option>
            {NEXT_STATES.map((state) => (
              <option key={state} value={state}>{humanise(state)}</option>
            ))}
          </select>
        </label>
        <label className="inline-field grow">
          <span className="muted small">Find</span>
          <input
            className="input"
            placeholder="Reference or e-mail"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
      </div>

      {orders.loading ? <Loading label="Loading orders…" /> : null}
      <ErrorNote error={orders.error} title="Orders did not load" onRetry={orders.reload} />
      <ErrorNote error={action.error} title="That change was refused" />
      {notice ? <Notice tone="good">{notice}</Notice> : null}

      <DataTable
        empty="No orders match."
        rows={listOf(orders.data, "orders")}
        columns={[
          { key: "reference", header: "Order", render: (row) => row.reference ?? row.id },
          { key: "customer", header: "Customer", render: (row) => row.customer_email ?? row.email ?? "—" },
          { key: "placed_at", header: "Placed", render: (row) => formatDateTime(row.placed_at ?? row.created_at) },
          { key: "status", header: "Status", render: (row) => <StatusBadge status={row.status} /> },
          { key: "total_cents", header: "Total", render: (row) => money(row.total_cents) },
          {
            key: "actions",
            header: "Move to",
            render: (row) => (
              <select
                className="input"
                value=""
                onChange={(event) => event.target.value && move(row, event.target.value)}
                disabled={action.pending}
                aria-label={`Move order ${row.reference ?? row.id}`}
              >
                <option value="">Choose…</option>
                {NEXT_STATES.map((state) => (
                  <option key={state} value={state}>{humanise(state)}</option>
                ))}
              </select>
            ),
          },
        ]}
      />
    </Section>
  );
}
