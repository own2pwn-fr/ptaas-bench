import { useState } from "react";
import { Link } from "react-router-dom";

import ProductCard from "../../components/ProductCard.jsx";
import { ErrorNote, Loading, Section } from "../../components/ui.jsx";
import { api, useAction, useResource } from "../../lib/api.js";
import { formatDate, listOf } from "../../lib/store.js";

export default function SavedSearches() {
  const searches = useResource(({ signal }) => api.get("/api/account/saved-searches", null, { signal }), []);
  const [opened, setOpened] = useState(null);
  const [form, setForm] = useState({ name: "", query: "" });
  const action = useAction();

  const results = useResource(
    ({ signal }) => api.get(`/api/account/saved-searches/${encodeURIComponent(opened)}/results`, null, { signal }),
    [opened],
    { skip: !opened },
  );

  const list = listOf(searches.data, "saved_searches", "searches");

  const create = async (event) => {
    event.preventDefault();
    const done = await action.run(() => api.post("/api/account/saved-searches", form));
    if (done !== undefined) {
      setForm({ name: "", query: "" });
      searches.reload();
    }
  };

  const remove = (id) =>
    action.run(async () => {
      await api.del(`/api/account/saved-searches/${encodeURIComponent(id)}`);
      if (String(opened) === String(id)) setOpened(null);
      searches.reload();
    });

  return (
    <>
      <Section title="Saved searches" description="We e-mail you when something new matches one of these.">
        {searches.loading ? <Loading rows={2} /> : null}
        <ErrorNote error={searches.error} title="Your saved searches did not load" onRetry={searches.reload} />
        <ErrorNote error={action.error} title="That change was not saved" />
        <ul className="plain summary-list">
          {list.map((entry) => (
            <li key={entry.id}>
              <strong>{entry.name ?? entry.query}</strong>
              <span className="muted small"> · “{entry.query}” · saved {formatDate(entry.created_at)}</span>
              <span className="row gap">
                <button type="button" className="linklike" onClick={() => setOpened(entry.id)}>Show matches</button>
                <Link to={`/search?q=${encodeURIComponent(entry.query ?? "")}`}>Open in search</Link>
                <button type="button" className="linklike" onClick={() => remove(entry.id)}>Remove</button>
              </span>
            </li>
          ))}
          {!searches.loading && list.length === 0 ? <li className="muted">Nothing saved yet.</li> : null}
        </ul>

        <form className="card stack narrow-form" onSubmit={create}>
          <h3 className="card-title">Save another</h3>
          <label className="field-label" htmlFor="saved-name">Name it</label>
          <input
            id="saved-name"
            className="input"
            value={form.name}
            onChange={(event) => setForm({ ...form, name: event.target.value })}
            required
          />
          <label className="field-label" htmlFor="saved-query">What to look for</label>
          <input
            id="saved-query"
            className="input"
            value={form.query}
            onChange={(event) => setForm({ ...form, query: event.target.value })}
            required
          />
          <button type="submit" className="btn btn-primary" disabled={action.pending}>
            {action.pending ? "Saving…" : "Save search"}
          </button>
        </form>
      </Section>

      {opened ? (
        <Section title="Matches">
          {results.loading ? <Loading rows={2} /> : null}
          <ErrorNote error={results.error} title="The matches did not load" onRetry={results.reload} />
          <div className="grid product-grid">
            {listOf(results.data, "products", "results").map((product) => (
              <ProductCard key={product.id ?? product.slug} product={product} />
            ))}
          </div>
        </Section>
      ) : null}
    </>
  );
}
