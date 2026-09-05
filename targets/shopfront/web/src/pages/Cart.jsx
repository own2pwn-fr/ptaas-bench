import { useEffect } from "react";
import { Link } from "react-router-dom";

import { EmptyState, ErrorNote, Loading, Section } from "../components/ui.jsx";
import { useCart } from "../lib/cart.jsx";
import { money } from "../lib/store.js";

function Line({ line, onChange, onRemove }) {
  const title = line.title ?? line.product_title ?? line.name ?? "Item";
  return (
    <li className="basket-line">
      <div className="basket-line-main">
        <p className="basket-line-title">
          {line.product_slug ? (
            <Link to={`/product/${encodeURIComponent(line.product_slug)}`}>{title}</Link>
          ) : (
            title
          )}
        </p>
        {line.variant_title ? <p className="muted small">{line.variant_title}</p> : null}
        <p className="muted small">{money(line.unit_price_cents)} each</p>
      </div>
      <label className="basket-qty">
        <span className="visually-hidden">Quantity for {title}</span>
        <input
          className="input narrow"
          type="number"
          min={0}
          max={99}
          value={line.quantity}
          onChange={(event) => onChange(line.id, Number(event.target.value))}
        />
      </label>
      <p className="basket-line-total">{money((line.unit_price_cents ?? 0) * (line.quantity ?? 0))}</p>
      <button type="button" className="linklike" onClick={() => onRemove(line.id)} data-track="basket-remove">
        Remove
      </button>
    </li>
  );
}

export default function Cart() {
  const { items, summary, loading, error, updateItem, removeItem, loadSummary, reload } = useCart();

  useEffect(() => {
    loadSummary();
  }, [loadSummary]);

  if (loading) return <Loading label="Loading your basket…" />;

  return (
    <Section title="Your basket">
      <ErrorNote error={error} title="The basket did not load" onRetry={reload} />
      {items.length === 0 ? (
        <EmptyState
          title="Your basket is empty"
          action={<Link className="btn btn-primary" to="/catalog">Start browsing</Link>}
        >
          Anything you add is kept for 30 days, on any device you sign in from.
        </EmptyState>
      ) : (
        <div className="basket">
          <ul className="plain basket-lines">
            {items.map((line) => (
              <Line
                key={line.id}
                line={line}
                onChange={(id, quantity) => (quantity <= 0 ? removeItem(id) : updateItem(id, quantity))}
                onRemove={removeItem}
              />
            ))}
          </ul>

          <aside className="card basket-summary">
            <h2 className="card-title">Summary</h2>
            <dl className="totals">
              <div>
                <dt>Items</dt>
                <dd>{money(summary?.subtotal_cents ?? summary?.items_cents ?? 0)}</dd>
              </div>
              <div>
                <dt>Delivery</dt>
                <dd>{summary?.shipping_cents ? money(summary.shipping_cents) : "Chosen at checkout"}</dd>
              </div>
              {summary?.discount_cents ? (
                <div>
                  <dt>Discount</dt>
                  <dd>−{money(summary.discount_cents)}</dd>
                </div>
              ) : null}
              <div className="total-row">
                <dt>Total</dt>
                <dd>{money(summary?.total_cents ?? summary?.subtotal_cents ?? 0)}</dd>
              </div>
            </dl>
            <Link className="btn btn-primary block" to="/checkout" data-track="go-to-checkout">
              Checkout
            </Link>
            <Link className="btn btn-quiet block" to="/catalog">
              Keep shopping
            </Link>
          </aside>
        </div>
      )}
    </Section>
  );
}
