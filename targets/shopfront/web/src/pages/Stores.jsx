import { useState } from "react";

import { ErrorNote, Loading, Section } from "../components/ui.jsx";
import { api, useResource } from "../lib/api.js";
import { listOf } from "../lib/store.js";

function OpeningHours({ hours }) {
  if (!hours) return null;
  const rows = Array.isArray(hours) ? hours : Object.entries(hours).map(([day, value]) => ({ day, hours: value }));
  return (
    <ul className="plain hours">
      {rows.map((row, index) => (
        <li key={row.day ?? index}>
          <span className="muted">{row.day ?? row.weekday}</span> {row.hours ?? row.opens ? `${row.opens}–${row.closes}` : String(row.hours ?? "")}
        </li>
      ))}
    </ul>
  );
}

export default function Stores() {
  const [selected, setSelected] = useState(null);
  const list = useResource(({ signal }) => api.get("/api/stores", null, { signal }), []);
  const detail = useResource(
    ({ signal }) => api.get(`/api/stores/${encodeURIComponent(selected)}`, null, { signal }),
    [selected],
    { skip: !selected },
  );

  const stores = listOf(list.data, "stores");
  const store = detail.data?.store ?? detail.data;

  return (
    <Section title="Our shops" description="Collect an order, return something, or get a repair looked at.">
      {list.loading ? <Loading label="Loading shops…" /> : null}
      <ErrorNote error={list.error} title="The shop list did not load" onRetry={list.reload} />
      <div className="two-col">
        <ul className="plain store-list">
          {stores.map((entry) => (
            <li key={entry.id}>
              <button
                type="button"
                className={`store-row${String(entry.id) === String(selected) ? " current" : ""}`}
                onClick={() => setSelected(entry.id)}
                data-track="store-select"
              >
                <span className="store-name">{entry.name ?? entry.title}</span>
                <span className="muted small">
                  {[entry.city, entry.postcode].filter(Boolean).join(" · ")}
                </span>
              </button>
            </li>
          ))}
          {!list.loading && stores.length === 0 ? <li className="muted">No shops listed yet.</li> : null}
        </ul>

        <div>
          {!selected ? <p className="muted">Pick a shop to see its address and opening hours.</p> : null}
          {detail.loading ? <Loading rows={2} /> : null}
          <ErrorNote error={detail.error} title="That shop did not load" onRetry={detail.reload} />
          {store ? (
            <article className="card">
              <h2 className="card-title">{store.name ?? store.title}</h2>
              <p className="muted">
                {[store.address_line1, store.address_line2, store.city, store.postcode, store.country]
                  .filter(Boolean)
                  .join(", ")}
              </p>
              {store.phone ? <p>{store.phone}</p> : null}
              {store.email ? <p>{store.email}</p> : null}
              <OpeningHours hours={store.opening_hours ?? store.hours} />
              {store.services?.length ? (
                <ul className="chips">
                  {store.services.map((service) => (
                    <li key={service}><span className="chip">{service}</span></li>
                  ))}
                </ul>
              ) : null}
            </article>
          ) : null}
        </div>
      </div>
    </Section>
  );
}
