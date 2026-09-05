/**
 * The catalogue grid.
 *
 * The grid reads from the graph endpoint: the tile needs six fields out of four tables
 * and the REST list was returning the whole product document for each one. The REST list
 * stays as the fallback while the graph rollout finishes.
 */
import { useCallback, useMemo, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";

import Pagination from "../components/Pagination.jsx";
import ProductCard from "../components/ProductCard.jsx";
import { ErrorNote, Loading, Section } from "../components/ui.jsx";
import { api, graphql, useResource } from "../lib/api.js";
import { listOf } from "../lib/store.js";

const GRID_QUERY = `query ProductGrid($category: String, $q: String, $sort: String, $page: Int, $limit: Int) {
  productGrid(category: $category, q: $q, sort: $sort, page: $page, limit: $limit) {
    total
    page
    pageCount
    items { id slug title brand priceCents currency rating reviewCount imageUrl inStock }
  }
}`;

const SORTS = [
  { value: "relevance", label: "Most relevant" },
  { value: "popular", label: "Most popular" },
  { value: "price_asc", label: "Price: low to high" },
  { value: "price_desc", label: "Price: high to low" },
  { value: "newest", label: "Newest" },
];

function normalise(item) {
  return {
    id: item.id,
    slug: item.slug,
    title: item.title ?? item.name,
    brand: item.brand,
    price_cents: item.priceCents ?? item.price_cents,
    currency: item.currency,
    rating: item.rating,
    review_count: item.reviewCount ?? item.review_count,
    image_url: item.imageUrl ?? item.image_url,
    in_stock: item.inStock ?? item.in_stock,
  };
}

export default function Catalog() {
  const { slug } = useParams();
  const [params, setParams] = useSearchParams();
  const page = Number(params.get("page") || 1);
  const sort = params.get("sort") || "relevance";
  const limit = 12;
  const [usedFallback, setUsedFallback] = useState(false);

  const load = useCallback(
    async ({ signal }) => {
      const variables = { category: slug ?? null, q: params.get("q") || null, sort, page, limit };
      try {
        const data = await graphql(GRID_QUERY, variables, "ProductGrid");
        if (data?.productGrid) {
          setUsedFallback(false);
          return data.productGrid;
        }
      } catch {
        // fall through to the list endpoint
      }
      setUsedFallback(true);
      const rest = await api.get(
        "/api/products",
        { q: params.get("q") || "", sort, page, limit, category: slug ?? "" },
        { signal },
      );
      const items = listOf(rest, "products");
      return {
        items,
        total: rest?.total ?? items.length,
        page: rest?.page ?? page,
        pageCount: rest?.page_count ?? rest?.pageCount ?? Math.max(1, Math.ceil((rest?.total ?? items.length) / limit)),
      };
    },
    [slug, sort, page, params],
  );

  const grid = useResource(load, [slug, sort, page, params.get("q")]);
  const categories = useResource(({ signal }) => api.get("/api/catalog/categories", null, { signal }), []);
  const items = useMemo(() => (grid.data?.items ?? []).map(normalise), [grid.data]);

  const setParam = (key, value) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, String(value));
    else next.delete(key);
    if (key !== "page") next.delete("page");
    setParams(next);
  };

  const categoryList = listOf(categories.data, "categories");
  const current = categoryList.find((c) => (c.slug ?? c.id) === slug);

  return (
    <div className="two-col">
      <aside className="side">
        <p className="side-title">Departments</p>
        <ul className="plain side-list">
          <li>
            <Link className={slug ? "" : "current"} to="/catalog">Everything</Link>
          </li>
          {categoryList.map((category) => {
            const key = category.slug ?? category.id;
            return (
              <li key={key}>
                <Link className={key === slug ? "current" : ""} to={`/catalog/${encodeURIComponent(key)}`}>
                  {category.title ?? category.name}
                </Link>
              </li>
            );
          })}
        </ul>
      </aside>

      <div>
        <Section
          title={current ? (current.title ?? current.name) : "Catalogue"}
          description={current?.description ?? "Everything we stock, in one list."}
          actions={
            <label className="inline-field">
              <span className="muted small">Sort</span>
              <select className="input" value={sort} onChange={(event) => setParam("sort", event.target.value)}>
                {SORTS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
          }
        >
          {grid.loading ? <Loading label="Loading the catalogue…" /> : null}
          <ErrorNote error={grid.error} title="The catalogue did not load" onRetry={grid.reload} />
          {!grid.loading && !grid.error && items.length === 0 ? (
            <p className="muted">Nothing in this department yet.</p>
          ) : null}
          <div className="grid product-grid">
            {items.map((product) => (
              <ProductCard key={product.id ?? product.slug} product={product} />
            ))}
          </div>
          <Pagination
            page={grid.data?.page ?? page}
            pageCount={grid.data?.pageCount ?? 1}
            total={grid.data?.total}
            onChange={(next) => setParam("page", next)}
          />
          {usedFallback ? <p className="muted small">Showing the standard list view.</p> : null}
        </Section>
      </div>
    </div>
  );
}
