/**
 * Checkout, in four steps.
 *
 * The server owns the session: every step posts to it and re-reads it, so a refresh in
 * the middle of the flow lands on the same step with the same totals.
 */
import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import StepUpDialog from "../components/StepUpDialog.jsx";
import { ErrorNote, Loading, Notice, Section } from "../components/ui.jsx";
import { api, useAction, useResource } from "../lib/api.js";
import { useCart } from "../lib/cart.jsx";
import { humanise, listOf, money } from "../lib/store.js";

const STEPS = ["Address", "Delivery", "Payment", "Review"];

function Stepper({ step, onGo }) {
  return (
    <ol className="stepper">
      {STEPS.map((label, index) => (
        <li key={label} className={index === step ? "current" : index < step ? "done" : ""}>
          <button type="button" className="linklike" onClick={() => index <= step && onGo(index)}>
            <span className="step-index">{index + 1}</span> {label}
          </button>
        </li>
      ))}
    </ol>
  );
}

function AddressStep({ session, onNext }) {
  const addresses = useResource(({ signal }) => api.get("/api/account/addresses", null, { signal }), []);
  const [chosen, setChosen] = useState(session?.shipping_address_id ?? null);
  const { pending, error, run } = useAction();
  const [form, setForm] = useState({
    full_name: "",
    line1: "",
    line2: "",
    city: "",
    postcode: "",
    country: "GB",
    phone: "",
  });

  const list = listOf(addresses.data, "addresses");

  const useExisting = () => run(async () => onNext({ shipping_address_id: chosen }));
  const useNew = (event) => {
    event.preventDefault();
    return run(async () => {
      const created = await api.post("/api/account/addresses", form);
      const address = created?.address ?? created;
      onNext({ shipping_address_id: address?.id ?? null, shipping_address: form });
    });
  };

  return (
    <div className="stack">
      <h2 className="card-title">Where is it going?</h2>
      {addresses.loading ? <Loading rows={2} /> : null}
      <ErrorNote error={addresses.error} title="Your addresses did not load" onRetry={addresses.reload} />
      {list.length ? (
        <>
          <ul className="plain choice-list">
            {list.map((address) => (
              <li key={address.id}>
                <label className="choice">
                  <input
                    type="radio"
                    name="address"
                    checked={String(chosen) === String(address.id)}
                    onChange={() => setChosen(address.id)}
                  />
                  <span>
                    <strong>{address.full_name ?? address.name}</strong>
                    <br />
                    {[address.line1, address.line2, address.city, address.postcode].filter(Boolean).join(", ")}
                  </span>
                </label>
              </li>
            ))}
          </ul>
          <button type="button" className="btn btn-primary" onClick={useExisting} disabled={pending || !chosen}>
            Deliver here
          </button>
        </>
      ) : null}

      <details className="disclosure" open={!list.length}>
        <summary>Use a new address</summary>
        <form className="stack" onSubmit={useNew}>
          {[
            ["full_name", "Full name"],
            ["line1", "Address"],
            ["line2", "Address line 2"],
            ["city", "Town or city"],
            ["postcode", "Postcode"],
            ["country", "Country"],
            ["phone", "Phone"],
          ].map(([key, label]) => (
            <span className="field" key={key}>
              <label className="field-label" htmlFor={`addr-${key}`}>{label}</label>
              <input
                id={`addr-${key}`}
                className="input"
                value={form[key]}
                onChange={(event) => setForm({ ...form, [key]: event.target.value })}
                required={["full_name", "line1", "city", "postcode", "country"].includes(key)}
              />
            </span>
          ))}
          <ErrorNote error={error} title="That address was not accepted" />
          <button type="submit" className="btn btn-primary" disabled={pending}>
            Save and continue
          </button>
        </form>
      </details>
    </div>
  );
}

function DeliveryStep({ onNext }) {
  const methods = useResource(({ signal }) => api.get("/api/shipping/methods", null, { signal }), []);
  const [chosen, setChosen] = useState(null);
  const { pending, error, run } = useAction();
  const list = listOf(methods.data, "methods", "shipping_methods");

  const choose = () =>
    run(async () => {
      const method = list.find((m) => String(m.id ?? m.code) === String(chosen));
      await api.post("/api/checkout/shipping", {
        method: method?.code ?? method?.id ?? chosen,
        rate_cents: method?.rate_cents ?? 0,
      });
      onNext();
    });

  return (
    <div className="stack">
      <h2 className="card-title">How should we send it?</h2>
      {methods.loading ? <Loading rows={2} /> : null}
      <ErrorNote error={methods.error} title="Delivery options did not load" onRetry={methods.reload} />
      <ul className="plain choice-list">
        {list.map((method) => {
          const key = method.id ?? method.code;
          return (
            <li key={key}>
              <label className="choice">
                <input
                  type="radio"
                  name="shipping"
                  checked={String(chosen) === String(key)}
                  onChange={() => setChosen(key)}
                />
                <span>
                  <strong>{method.title ?? method.name}</strong> — {money(method.rate_cents)}
                  <br />
                  <span className="muted small">{method.description ?? method.eta ?? ""}</span>
                </span>
              </label>
            </li>
          );
        })}
      </ul>
      <ErrorNote error={error} title="We could not set that delivery option" />
      <button type="button" className="btn btn-primary" onClick={choose} disabled={pending || !chosen}>
        Continue
      </button>
    </div>
  );
}

function PaymentStep({ session, onNext, onReload }) {
  const cards = useResource(({ signal }) => api.get("/api/account/payment-methods", null, { signal }), []);
  const [code, setCode] = useState("");
  const [chosen, setChosen] = useState(null);
  const coupon = useAction();
  const list = listOf(cards.data, "payment_methods", "methods", "cards");
  const applied = listOf(session, "coupons", "discounts");

  const apply = async (event) => {
    event.preventDefault();
    const done = await coupon.run(() => api.post("/api/checkout/coupons", { code: code.trim() }));
    if (done !== undefined) {
      setCode("");
      onReload();
    }
  };

  const drop = (value) =>
    coupon.run(async () => {
      await api.del(`/api/checkout/coupons/${encodeURIComponent(value)}`);
      onReload();
    });

  return (
    <div className="stack">
      <h2 className="card-title">How are you paying?</h2>
      {cards.loading ? <Loading rows={2} /> : null}
      <ErrorNote error={cards.error} title="Your saved cards did not load" onRetry={cards.reload} />
      <ul className="plain choice-list">
        {list.map((card) => (
          <li key={card.id}>
            <label className="choice">
              <input
                type="radio"
                name="card"
                checked={String(chosen) === String(card.id)}
                onChange={() => setChosen(card.id)}
              />
              <span>
                {humanise(card.brand ?? card.scheme)} ending {card.last4 ?? card.last_four}
                <span className="muted small"> · expires {card.exp_month}/{card.exp_year}</span>
              </span>
            </label>
          </li>
        ))}
        <li>
          <label className="choice">
            <input type="radio" name="card" checked={chosen === "new"} onChange={() => setChosen("new")} />
            <span>Pay on the next screen with a new card</span>
          </label>
        </li>
      </ul>

      <form className="row gap" onSubmit={apply}>
        <input
          className="input"
          placeholder="Discount code"
          aria-label="Discount code"
          value={code}
          onChange={(event) => setCode(event.target.value)}
        />
        <button type="submit" className="btn btn-quiet" disabled={coupon.pending || !code.trim()}>
          Apply
        </button>
      </form>
      <ErrorNote error={coupon.error} title="That code was not applied" />
      {applied.length ? (
        <ul className="plain">
          {applied.map((entry) => {
            const value = entry.code ?? entry;
            return (
              <li key={value}>
                <span className="tag">{value}</span>{" "}
                <button type="button" className="linklike" onClick={() => drop(value)}>
                  Remove
                </button>
              </li>
            );
          })}
        </ul>
      ) : null}

      <button type="button" className="btn btn-primary" onClick={() => onNext({ payment_method_id: chosen })} disabled={!chosen}>
        Review the order
      </button>
    </div>
  );
}

function ReviewStep({ session, onConfirm, confirming, error }) {
  const tax = useResource(({ signal }) => api.get("/api/checkout/tax-estimate", null, { signal }), []);
  const totals = session?.totals ?? session ?? {};
  const estimate = tax.data?.tax_cents ?? tax.data?.estimate_cents;

  return (
    <div className="stack">
      <h2 className="card-title">Check it over</h2>
      <dl className="totals">
        <div>
          <dt>Items</dt>
          <dd>{money(totals.subtotal_cents ?? 0)}</dd>
        </div>
        <div>
          <dt>Delivery</dt>
          <dd>{money(totals.shipping_cents ?? 0)}</dd>
        </div>
        {totals.discount_cents ? (
          <div>
            <dt>Discount</dt>
            <dd>−{money(totals.discount_cents)}</dd>
          </div>
        ) : null}
        <div>
          <dt>VAT</dt>
          <dd>{tax.loading ? "…" : money(estimate ?? totals.tax_cents ?? 0)}</dd>
        </div>
        <div className="total-row">
          <dt>Total</dt>
          <dd>{money(totals.total_cents ?? 0)}</dd>
        </div>
      </dl>
      <ErrorNote error={error} title="The order was not placed" />
      <button type="button" className="btn btn-primary" onClick={onConfirm} disabled={confirming} data-track="confirm-order">
        {confirming ? "Placing the order…" : "Place the order"}
      </button>
      <p className="muted small">
        By placing the order you accept our <Link to="/pages/terms">terms</Link>.
      </p>
    </div>
  );
}

export default function Checkout() {
  const { items, loading: cartLoading } = useCart();
  const navigate = useNavigate();
  const [step, setStep] = useState(0);
  const [sessionId, setSessionId] = useState(null);
  const [session, setSession] = useState(null);
  const [startError, setStartError] = useState(null);
  const [needsStepUp, setNeedsStepUp] = useState(false);
  const confirm = useAction();

  const readSession = useCallback(async (id) => {
    const data = await api.get(`/api/checkout/sessions/${encodeURIComponent(id)}`);
    setSession(data?.session ?? data);
  }, []);

  useEffect(() => {
    let live = true;
    api
      .post("/api/checkout/sessions", {})
      .then((data) => {
        if (!live) return;
        const created = data?.session ?? data;
        setSession(created);
        setSessionId(created?.id ?? null);
      })
      .catch((caught) => live && setStartError(caught));
    return () => {
      live = false;
    };
  }, []);

  const reload = useCallback(() => {
    if (sessionId) readSession(sessionId).catch(() => {});
  }, [sessionId, readSession]);

  const place = async () => {
    const result = await confirm.run(() => api.post("/api/checkout/confirm", { session_id: sessionId }));
    if (result === undefined) return;
    const order = result?.order ?? result;
    if (order?.id) navigate(`/account/orders/${encodeURIComponent(order.id)}`);
    else navigate("/account/orders");
  };

  useEffect(() => {
    if (confirm.error?.code === "step_up_required" || confirm.error?.status === 428) setNeedsStepUp(true);
  }, [confirm.error]);

  if (cartLoading) return <Loading label="Loading your basket…" />;
  if (!items.length) {
    return (
      <Section title="Checkout">
        <Notice>
          Your basket is empty. <Link to="/catalog">Find something first.</Link>
        </Notice>
      </Section>
    );
  }

  return (
    <Section title="Checkout">
      <ErrorNote error={startError} title="We could not start the checkout" />
      <Stepper step={step} onGo={setStep} />
      <div className="checkout-body">
        <div className="card">
          {step === 0 ? <AddressStep session={session} onNext={() => setStep(1)} /> : null}
          {step === 1 ? <DeliveryStep onNext={() => { reload(); setStep(2); }} /> : null}
          {step === 2 ? <PaymentStep session={session} onReload={reload} onNext={() => { reload(); setStep(3); }} /> : null}
          {step === 3 ? (
            <ReviewStep session={session} onConfirm={place} confirming={confirm.pending} error={confirm.error} />
          ) : null}
        </div>
        <aside className="card">
          <h2 className="card-title">Your basket</h2>
          <ul className="plain">
            {items.map((line) => (
              <li key={line.id} className="mini-line">
                <span>{line.quantity} × {line.title ?? line.product_title ?? "Item"}</span>
                <span>{money((line.unit_price_cents ?? 0) * (line.quantity ?? 0))}</span>
              </li>
            ))}
          </ul>
          <Link className="btn btn-quiet block" to="/cart">Edit the basket</Link>
        </aside>
      </div>
      {needsStepUp ? (
        <StepUpDialog
          purpose="checkout_confirm"
          onCancel={() => setNeedsStepUp(false)}
          onDone={() => {
            setNeedsStepUp(false);
            place();
          }}
        />
      ) : null}
    </Section>
  );
}
