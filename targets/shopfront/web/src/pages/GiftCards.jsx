import { useState } from "react";
import { Link } from "react-router-dom";

import { ErrorNote, Notice, Section } from "../components/ui.jsx";
import { api, useAction, useResource } from "../lib/api.js";
import { useSession } from "../lib/session.jsx";
import { money } from "../lib/store.js";

const AMOUNTS = [2500, 5000, 10000, 25000];

export default function GiftCards() {
  const { signedIn } = useSession();
  const [code, setCode] = useState("");
  const [redeemed, setRedeemed] = useState(null);
  const redeem = useAction();
  const wallet = useResource(({ signal }) => api.get("/api/wallet", null, { signal }), [redeemed], {
    skip: !signedIn,
  });

  const submit = async (event) => {
    event.preventDefault();
    const result = await redeem.run(() => api.post("/api/gift-cards/redeem", { code: code.trim() }));
    if (result !== undefined) {
      setRedeemed(result);
      setCode("");
    }
  };

  const balance = wallet.data?.balance_cents ?? wallet.data?.wallet?.balance_cents;

  return (
    <>
      <Section title="Gift cards" description="Good for two years, usable in the shops and online.">
        <div className="grid amount-grid">
          {AMOUNTS.map((amount) => (
            <article className="card amount-card" key={amount}>
              <p className="amount">{money(amount)}</p>
              <p className="muted small">Delivered by e-mail, or printed and posted.</p>
              <Link className="btn btn-quiet" to={`/search?q=gift%20card&amount=${amount}`}>
                Buy this amount
              </Link>
            </article>
          ))}
        </div>
      </Section>

      <Section title="Redeem a card" description="The code is on the back, or in the e-mail we sent.">
        <form className="card stack narrow-form" onSubmit={submit}>
          <label className="field-label" htmlFor="gift-code">Card code</label>
          <input
            id="gift-code"
            className="input"
            value={code}
            onChange={(event) => setCode(event.target.value)}
            placeholder="XXXX-XXXX-XXXX"
            required
          />
          <ErrorNote error={redeem.error} title="That code was not accepted" />
          {redeemed ? (
            <Notice tone="good">
              Added {money(redeemed.amount_cents ?? redeemed.credited_cents ?? 0)} to your balance.
            </Notice>
          ) : null}
          <button type="submit" className="btn btn-primary" disabled={redeem.pending} data-track="gift-redeem">
            {redeem.pending ? "Checking…" : "Redeem"}
          </button>
          {!signedIn ? (
            <p className="muted small">
              <Link to="/sign-in">Sign in</Link> first and the credit lands on your account.
            </p>
          ) : null}
        </form>
        {signedIn && Number.isFinite(Number(balance)) ? (
          <p className="muted">
            Your balance is {money(balance)}. <Link to="/account/wallet">See your wallet</Link>
          </p>
        ) : null}
      </Section>
    </>
  );
}
