import { useState } from "react";

import { ErrorNote, Loading, Notice, Section } from "../../components/ui.jsx";
import { api, useAction, useResource } from "../../lib/api.js";
import { listOf } from "../../lib/store.js";

const BLANK = { label: "", full_name: "", line1: "", line2: "", city: "", postcode: "", country: "GB", phone: "" };
const FIELDS = [
  ["label", "Label (home, work…)"],
  ["full_name", "Full name"],
  ["line1", "Address"],
  ["line2", "Address line 2"],
  ["city", "Town or city"],
  ["postcode", "Postcode"],
  ["country", "Country"],
  ["phone", "Phone"],
];

export default function Addresses() {
  const addresses = useResource(({ signal }) => api.get("/api/account/addresses", null, { signal }), []);
  const [editing, setEditing] = useState(null);
  const [form, setForm] = useState(BLANK);
  const action = useAction();
  const [notice, setNotice] = useState(null);

  const list = listOf(addresses.data, "addresses");

  const startNew = () => {
    setEditing("new");
    setForm(BLANK);
  };

  const startEdit = (address) => {
    setEditing(address.id);
    setForm({ ...BLANK, ...address });
  };

  const submit = async (event) => {
    event.preventDefault();
    const body = Object.fromEntries(FIELDS.map(([key]) => [key, form[key] ?? ""]));
    const done = await action.run(() =>
      editing === "new"
        ? api.post("/api/account/addresses", body)
        : api.patch(`/api/account/addresses/${encodeURIComponent(editing)}`, body),
    );
    if (done !== undefined) {
      setNotice(editing === "new" ? "Address added." : "Address updated.");
      setEditing(null);
      addresses.reload();
    }
  };

  const remove = (id) =>
    action.run(async () => {
      await api.del(`/api/account/addresses/${encodeURIComponent(id)}`);
      setNotice("Address removed.");
      addresses.reload();
    });

  const setDefault = (address) =>
    action.run(async () => {
      await api.patch(`/api/account/addresses/${encodeURIComponent(address.id)}`, { is_default: true });
      addresses.reload();
    });

  return (
    <Section
      title="Addresses"
      description="Where we send things, and where the card statements go."
      actions={<button type="button" className="btn btn-primary" onClick={startNew}>Add an address</button>}
    >
      {addresses.loading ? <Loading rows={2} /> : null}
      <ErrorNote error={addresses.error} title="Your addresses did not load" onRetry={addresses.reload} />
      <ErrorNote error={action.error} title="That change was not saved" />
      {notice ? <Notice tone="good">{notice}</Notice> : null}

      <div className="grid two">
        {list.map((address) => (
          <article className="card" key={address.id}>
            <h3 className="card-title">
              {address.label || address.full_name}
              {address.is_default ? <span className="tag">Default</span> : null}
            </h3>
            <p className="muted">
              {[address.full_name, address.line1, address.line2, address.city, address.postcode, address.country]
                .filter(Boolean)
                .join(", ")}
            </p>
            {address.phone ? <p className="muted small">{address.phone}</p> : null}
            <p className="row gap">
              <button type="button" className="linklike" onClick={() => startEdit(address)}>Edit</button>
              {!address.is_default ? (
                <button type="button" className="linklike" onClick={() => setDefault(address)}>Make default</button>
              ) : null}
              <button type="button" className="linklike" onClick={() => remove(address.id)}>Remove</button>
            </p>
          </article>
        ))}
        {!addresses.loading && list.length === 0 ? <p className="muted">No addresses saved yet.</p> : null}
      </div>

      {editing ? (
        <form className="card stack" onSubmit={submit}>
          <h3 className="card-title">{editing === "new" ? "New address" : "Edit address"}</h3>
          {FIELDS.map(([key, label]) => (
            <span className="field" key={key}>
              <label className="field-label" htmlFor={`address-${key}`}>{label}</label>
              <input
                id={`address-${key}`}
                className="input"
                value={form[key] ?? ""}
                onChange={(event) => setForm({ ...form, [key]: event.target.value })}
                required={["full_name", "line1", "city", "postcode", "country"].includes(key)}
              />
            </span>
          ))}
          <div className="row gap">
            <button type="submit" className="btn btn-primary" disabled={action.pending}>
              {action.pending ? "Saving…" : "Save"}
            </button>
            <button type="button" className="btn btn-quiet" onClick={() => setEditing(null)}>Cancel</button>
          </div>
        </form>
      ) : null}
    </Section>
  );
}
