import { useState } from "react";
import { Link } from "react-router-dom";

import StepUpDialog from "../../components/StepUpDialog.jsx";
import { ErrorNote, Loading, Notice, Section } from "../../components/ui.jsx";
import { api, useResource } from "../../lib/api.js";
import { humanise, listOf } from "../../lib/store.js";

export default function PaymentMethods() {
  const methods = useResource(({ signal }) => api.get("/api/account/payment-methods", null, { signal }), []);
  const [confirming, setConfirming] = useState(false);
  const [unlocked, setUnlocked] = useState(false);

  const list = listOf(methods.data, "payment_methods", "methods", "cards");

  return (
    <Section
      title="Payment methods"
      description="Cards are held by our payment provider; we only keep the last four digits."
      actions={
        <button type="button" className="btn btn-quiet" onClick={() => setConfirming(true)}>
          Add a card
        </button>
      }
    >
      {methods.loading ? <Loading rows={2} /> : null}
      <ErrorNote error={methods.error} title="Your cards did not load" onRetry={methods.reload} />
      {unlocked ? (
        <Notice tone="good">
          Confirmed. Add the card on the next screen — we will not charge it until you order.
        </Notice>
      ) : null}

      <ul className="plain card-list">
        {list.map((card) => (
          <li className="card payment-row" key={card.id}>
            <span>
              <strong>{humanise(card.brand ?? card.scheme)}</strong> ending {card.last4 ?? card.last_four}
              <span className="muted small"> · expires {card.exp_month}/{card.exp_year}</span>
            </span>
            {card.is_default ? <span className="tag">Default</span> : null}
          </li>
        ))}
        {!methods.loading && list.length === 0 ? <li className="muted">No cards saved.</li> : null}
      </ul>

      <p className="muted small">
        Gift card credit is kept separately in your <Link to="/account/wallet">wallet</Link>.
      </p>

      {confirming ? (
        <StepUpDialog
          purpose="add_payment_method"
          title="Confirm before adding a card"
          onCancel={() => setConfirming(false)}
          onDone={() => {
            setConfirming(false);
            setUnlocked(true);
            methods.reload();
          }}
        />
      ) : null}
    </Section>
  );
}
