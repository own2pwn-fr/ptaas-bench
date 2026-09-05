import { useState } from "react";
import { Link, useParams } from "react-router-dom";

import { DataTable, ErrorNote, Loading, Notice, Section, StatusBadge } from "../../components/ui.jsx";
import { api, useAction, useResource } from "../../lib/api.js";
import { formatDate, formatDateTime, humanise, listOf, money } from "../../lib/store.js";

export default function OrderDetail() {
  const { id } = useParams();
  const [returnReason, setReturnReason] = useState("");
  const action = useAction();
  const [done, setDone] = useState(null);

  const order = useResource(({ signal }) => api.get(`/api/orders/${encodeURIComponent(id)}`, null, { signal }), [id]);
  const items = useResource(
    ({ signal }) => api.get(`/api/orders/${encodeURIComponent(id)}/items`, null, { signal }),
    [id],
  );
  const shipments = useResource(
    ({ signal }) => api.get(`/api/orders/${encodeURIComponent(id)}/shipments`, null, { signal }),
    [id],
  );

  if (order.loading) return <Loading label="Loading the order…" />;
  if (order.error) return <ErrorNote error={order.error} title="That order did not load" onRetry={order.reload} />;

  const detail = order.data?.order ?? order.data;
  const lines = listOf(items.data, "items", "lines");
  const parcels = listOf(shipments.data, "shipments");
  const nextStates = listOf(detail, "available_transitions", "next_states");

  const move = (to) =>
    action.run(async () => {
      await api.post(`/api/orders/${encodeURIComponent(id)}/transitions`, { to });
      setDone(`Order marked as ${humanise(to).toLowerCase()}.`);
      order.reload();
    });

  const requestReturn = async (event) => {
    event.preventDefault();
    const result = await action.run(() =>
      api.post(`/api/orders/${encodeURIComponent(id)}/returns`, {
        reason: returnReason,
        item_ids: lines.map((line) => line.id),
      }),
    );
    if (result !== undefined) {
      setDone("We have started the return. A label is on its way by e-mail.");
      setReturnReason("");
    }
  };

  return (
    <>
      <p className="crumbs">
        <Link to="/account/orders">Orders</Link>
        <span aria-hidden="true"> / </span>
        <span>{detail?.reference ?? id}</span>
      </p>

      <Section
        title={`Order ${detail?.reference ?? id}`}
        description={`Placed ${formatDate(detail?.placed_at ?? detail?.created_at)}`}
        actions={<StatusBadge status={detail?.status} />}
      >
        {done ? <Notice tone="good">{done}</Notice> : null}
        <ErrorNote error={action.error} title="That did not go through" />

        <div className="grid two">
          <article className="card">
            <h3 className="card-title">Delivery address</h3>
            <p className="muted">
              {[
                detail?.shipping_address?.full_name,
                detail?.shipping_address?.line1,
                detail?.shipping_address?.line2,
                detail?.shipping_address?.city,
                detail?.shipping_address?.postcode,
              ]
                .filter(Boolean)
                .join(", ") || "—"}
            </p>
          </article>
          <article className="card">
            <h3 className="card-title">Totals</h3>
            <dl className="totals">
              <div><dt>Items</dt><dd>{money(detail?.subtotal_cents)}</dd></div>
              <div><dt>Delivery</dt><dd>{money(detail?.shipping_cents)}</dd></div>
              {detail?.discount_cents ? <div><dt>Discount</dt><dd>−{money(detail.discount_cents)}</dd></div> : null}
              <div className="total-row"><dt>Total</dt><dd>{money(detail?.total_cents)}</dd></div>
            </dl>
          </article>
        </div>
      </Section>

      <Section title="What you ordered">
        {items.loading ? <Loading rows={2} /> : null}
        <ErrorNote error={items.error} title="The order lines did not load" onRetry={items.reload} />
        <DataTable
          empty="No lines on this order."
          rows={lines}
          columns={[
            { key: "title", header: "Item", render: (row) => row.title ?? row.product_title ?? row.name },
            { key: "variant_title", header: "Option", render: (row) => row.variant_title ?? "—" },
            { key: "quantity", header: "Qty", render: (row) => String(row.quantity ?? 1) },
            { key: "unit_price_cents", header: "Each", render: (row) => money(row.unit_price_cents) },
            {
              key: "total",
              header: "Line total",
              render: (row) => money((row.unit_price_cents ?? 0) * (row.quantity ?? 1)),
            },
          ]}
        />
      </Section>

      <Section title="Parcels">
        {shipments.loading ? <Loading rows={1} /> : null}
        <ErrorNote error={shipments.error} title="Delivery information did not load" onRetry={shipments.reload} />
        <ul className="plain summary-list">
          {parcels.map((parcel) => (
            <li key={parcel.id}>
              <strong>{parcel.carrier ?? "Courier"}</strong>{" "}
              {parcel.tracking_number ? <span className="tag">{parcel.tracking_number}</span> : null}{" "}
              <StatusBadge status={parcel.status} />
              <span className="muted small"> · {formatDateTime(parcel.dispatched_at ?? parcel.created_at)}</span>
            </li>
          ))}
          {!shipments.loading && parcels.length === 0 ? <li className="muted">Nothing has left us yet.</li> : null}
        </ul>
      </Section>

      {nextStates.length ? (
        <Section title="Update this order">
          <div className="row gap">
            {nextStates.map((state) => {
              const value = state.to ?? state;
              return (
                <button
                  key={value}
                  type="button"
                  className="btn btn-quiet"
                  onClick={() => move(value)}
                  disabled={action.pending}
                >
                  {humanise(value)}
                </button>
              );
            })}
          </div>
        </Section>
      ) : null}

      <Section title="Send something back" description="Thirty days, any reason, free return label.">
        <form className="card stack" onSubmit={requestReturn}>
          <label className="field-label" htmlFor="return-reason">Why is it going back?</label>
          <textarea
            id="return-reason"
            className="input"
            rows={4}
            value={returnReason}
            onChange={(event) => setReturnReason(event.target.value)}
            required
          />
          <button type="submit" className="btn btn-primary" disabled={action.pending} data-track="order-return">
            {action.pending ? "Sending…" : "Start a return"}
          </button>
        </form>
      </Section>
    </>
  );
}
