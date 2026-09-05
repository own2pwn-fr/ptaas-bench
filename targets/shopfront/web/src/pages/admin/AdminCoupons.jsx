import { useState } from "react";

import { DataTable, ErrorNote, Loading, Notice, Section, StatusBadge } from "../../components/ui.jsx";
import { api, useAction, useResource } from "../../lib/api.js";
import { formatDate, listOf, money } from "../../lib/store.js";

const BLANK = { code: "", kind: "percent", value: 10, minimum_cents: 0, expires_at: "", usage_limit: 100 };

export default function AdminCoupons() {
  const coupons = useResource(({ signal }) => api.get("/api/admin/coupons", null, { signal }), []);
  const [form, setForm] = useState(BLANK);
  const action = useAction();
  const [notice, setNotice] = useState(null);

  const submit = async (event) => {
    event.preventDefault();
    const done = await action.run(() =>
      api.post("/api/admin/coupons", {
        ...form,
        value: Number(form.value),
        minimum_cents: Number(form.minimum_cents),
        usage_limit: Number(form.usage_limit),
      }),
    );
    if (done !== undefined) {
      setNotice(`Code ${form.code} created.`);
      setForm(BLANK);
      coupons.reload();
    }
  };

  return (
    <>
      <Section title="Discount codes">
        {coupons.loading ? <Loading rows={2} /> : null}
        <ErrorNote error={coupons.error} title="The codes did not load" onRetry={coupons.reload} />
        <DataTable
          empty="No codes yet."
          rows={listOf(coupons.data, "coupons")}
          columns={[
            { key: "code", header: "Code", render: (row) => <span className="tag">{row.code}</span> },
            {
              key: "value",
              header: "Takes off",
              render: (row) => (row.kind === "amount" ? money(row.value) : `${row.value}%`),
            },
            { key: "minimum_cents", header: "Minimum", render: (row) => money(row.minimum_cents) },
            { key: "used", header: "Used", render: (row) => `${row.used_count ?? 0}/${row.usage_limit ?? "∞"}` },
            { key: "expires_at", header: "Expires", render: (row) => formatDate(row.expires_at) },
            { key: "status", header: "State", render: (row) => <StatusBadge status={row.status ?? "active"} /> },
          ]}
        />
      </Section>

      <Section title="New code">
        <form className="card stack narrow-form" onSubmit={submit}>
          {notice ? <Notice tone="good">{notice}</Notice> : null}
          <span className="field">
            <label className="field-label" htmlFor="coupon-code">Code</label>
            <input
              id="coupon-code"
              className="input"
              value={form.code}
              onChange={(event) => setForm({ ...form, code: event.target.value.toUpperCase() })}
              required
            />
          </span>
          <span className="field">
            <label className="field-label" htmlFor="coupon-kind">Type</label>
            <select
              id="coupon-kind"
              className="input"
              value={form.kind}
              onChange={(event) => setForm({ ...form, kind: event.target.value })}
            >
              <option value="percent">Percentage off</option>
              <option value="amount">Fixed amount off</option>
              <option value="shipping">Free delivery</option>
            </select>
          </span>
          <span className="field">
            <label className="field-label" htmlFor="coupon-value">
              {form.kind === "amount" ? "Amount off (minor units)" : "Percentage off"}
            </label>
            <input
              id="coupon-value"
              className="input"
              type="number"
              min={0}
              value={form.value}
              onChange={(event) => setForm({ ...form, value: event.target.value })}
            />
          </span>
          <span className="field">
            <label className="field-label" htmlFor="coupon-min">Minimum basket (minor units)</label>
            <input
              id="coupon-min"
              className="input"
              type="number"
              min={0}
              value={form.minimum_cents}
              onChange={(event) => setForm({ ...form, minimum_cents: event.target.value })}
            />
          </span>
          <span className="field">
            <label className="field-label" htmlFor="coupon-limit">How many times it can be used</label>
            <input
              id="coupon-limit"
              className="input"
              type="number"
              min={1}
              value={form.usage_limit}
              onChange={(event) => setForm({ ...form, usage_limit: event.target.value })}
            />
          </span>
          <span className="field">
            <label className="field-label" htmlFor="coupon-expiry">Expires</label>
            <input
              id="coupon-expiry"
              className="input"
              type="date"
              value={form.expires_at}
              onChange={(event) => setForm({ ...form, expires_at: event.target.value })}
            />
          </span>
          <ErrorNote error={action.error} title="The code was not created" />
          <button type="submit" className="btn btn-primary" disabled={action.pending}>
            {action.pending ? "Creating…" : "Create code"}
          </button>
        </form>
      </Section>
    </>
  );
}
