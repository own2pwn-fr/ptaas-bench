import { useState } from "react";
import { useSearchParams } from "react-router-dom";

import Pagination from "../components/Pagination.jsx";
import ProductCard from "../components/ProductCard.jsx";
import { ErrorNote, Loading, Notice, Section } from "../components/ui.jsx";
import { api, useAction, useResource } from "../lib/api.js";
import { useSession } from "../lib/session.jsx";
import { listOf } from "../lib/store.js";

const SORTS = [
  { value: "relevance", label: "Most relevant" },
  { value: "price_asc", label: "Price: low to high" },
  { value: "price_desc", label: "Price: high to low" },
  { value: "newest", label: "Newest" },
];

export default function Search() {
  const [params, setParams] = useSearchParams();
  const q = params.get("q") ?? "";
  const sort = params.get("sort") ?? "relevance";
  const page = Number(params.get("page") || 1);
  const ref = params.get("ref");
  const { signedIn } = useSession();
  const save = useAction();
  const [saved, setSaved] = useState(false);

  const results = useResource(
    ({ signal }) => api.get("/api/products", { q, sort, page, limit: 12 }, { signal }),
    [q, sort, page],
  );

  const setParam = (key, value) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, String(value));
    else next.delete(key);
    if (key !== "page") next.delete("page");
    setParams(next);
  };

  const products = listOf(results.data, "products");
  const total = results.data?.total ?? products.length;
  const pageCount = results.data?.page_count ?? results.data?.pageCount ?? Math.max(1, Math.ceil(total / 12));

  const saveSearch = async () => {
    const done = await save.run(() =>
      api.post("/api/account/saved-searches", { name: q || "All products", query: q, sort }),
    );
    if (done !== undefined) setSaved(true);
  };

  return (
    <>
      {/*
        Where the visitor came from. Campaign metadata carries its own light formatting —
        the newsletter names the promotion in <em> — so the crumb is placed as markup
        rather than as text, otherwise the tags show up in the sentence.
      */}
      {ref ? <p className="crumb-banner" dangerouslySetInnerHTML={{ __html: ref }} /> : null}

      <Section
        title={q ? `Results for “${q}”` : "Search"}
        description={q ? `${total} item${total === 1 ? "" : "s"} matched.` : "Type what you are after in the box above."}
        actions={
          <label className="inline-field">
            <span className="muted small">Sort</span>
            <select className="input" value={sort} onChange={(event) => setParam("sort", event.target.value)}>
              {SORTS.map((option) => (
                <option key={option.value} value={option.value}>{option.label}</option>
              ))}
            </select>
          </label>
        }
      >
        {results.loading ? <Loading label="Searching…" /> : null}
        <ErrorNote error={results.error} title="The search did not run" onRetry={results.reload} />
        {!results.loading && !results.error && q && products.length === 0 ? (
          <p className="muted">Nothing matched. Try a shorter word, or browse the catalogue.</p>
        ) : null}

        <div className="grid product-grid">
          {products.map((product) => (
            <ProductCard key={product.id ?? product.slug} product={product} />
          ))}
        </div>

        <Pagination page={page} pageCount={pageCount} total={total} onChange={(next) => setParam("page", next)} />

        {signedIn && q ? (
          <p className="row gap">
            <button type="button" className="btn btn-quiet" onClick={saveSearch} disabled={save.pending || saved} data-track="save-search">
              {saved ? "Search saved" : "Save this search"}
            </button>
          </p>
        ) : null}
        <ErrorNote error={save.error} title="We could not save that search" />
        {saved ? <Notice tone="good">We will e-mail you when something new matches.</Notice> : null}
      </Section>
    </>
  );
}
