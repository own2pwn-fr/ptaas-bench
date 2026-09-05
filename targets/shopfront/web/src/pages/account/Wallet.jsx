import { useState } from "react";

import { DataTable, ErrorNote, Loading, Notice, Section } from "../../components/ui.jsx";
import { api, useAction, useResource } from "../../lib/api.js";
import { formatDateTime, humanise, listOf, money } from "../../lib/store.js";

export default function Wallet() {
  const wallet = useResource(({ signal }) => api.get("/api/wallet", null, { signal }), []);
  const [reloadKey, setReloadKey] = useState(0);
  const transactions = useResource(
    ({ signal }) => api.get("/api/wallet/transactions", null, { signal }),
    [reloadKey],
  );
  const [code, setCode] = useState("");
  const redeem = useAction();
  const [notice, setNotice] = useState(null);

  const account = wallet.data?.wallet ?? wallet.data ?? {};

  const submit = async (event) => {
    event.preventDefault();
    const result = await redeem.run(() => api.post("/api/gift-cards/redeem", { code: code.trim() }));
    if (result !== undefined) {
      setNotice(`Added ${money(result?.amount_cents ?? result?.credited_cents ?? 0)} to your balance.`);
      setCode("");
      wallet.reload();
      setReloadKey((n) => n + 1);
    }
  };

  return (
    <>
      <Section title="Wallet" description="Gift card credit and refunds live here.">
        {wallet.loading ? <Loading rows={1} /> : null}
        <ErrorNote error={wallet.error} title="Your wallet did not load" onRetry={wallet.reload} />
        <p className="stat">{money(account.balance_cents ?? 0, account.currency)}</p>
        <p className="muted small">Spent automatically at checkout before your card is charged.</p>

        <form className="row gap" onSubmit={submit}>
          <input
            className="input"
            placeholder="Gift card code"
            aria-label="Gift card code"
            value={code}
            onChange={(event) => setCode(event.target.value)}
          />
          <button type="submit" className="btn btn-primary" disabled={redeem.pending || !code.trim()}>
            {redeem.pending ? "Checking…" : "Add credit"}
          </button>
        </form>
        <ErrorNote error={redeem.error} title="That code was not accepted" />
        {notice ? <Notice tone="good">{notice}</Notice> : null}
      </Section>

      <Section title="Movements">
        {transactions.loading ? <Loading rows={2} /> : null}
        <ErrorNote error={transactions.error} title="The movements did not load" onRetry={transactions.reload} />
        <DataTable
          empty="Nothing has moved in or out yet."
          rows={listOf(transactions.data, "transactions")}
          columns={[
            { key: "created_at", header: "When", render: (row) => formatDateTime(row.created_at) },
            { key: "kind", header: "Type", render: (row) => humanise(row.kind ?? row.type) },
            { key: "description", header: "Detail", render: (row) => row.description ?? row.reference ?? "—" },
            {
              key: "amount_cents",
              header: "Amount",
              render: (row) => `${(row.amount_cents ?? 0) < 0 ? "−" : "+"}${money(Math.abs(row.amount_cents ?? 0))}`,
            },
          ]}
        />
      </Section>
    </>
  );
}
