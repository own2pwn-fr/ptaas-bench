import { Link } from "react-router-dom";

import ProductCard from "../components/ProductCard.jsx";
import { ErrorNote, Loading, Section } from "../components/ui.jsx";
import { api, useResource } from "../lib/api.js";
import { listOf, storeName } from "../lib/store.js";

export default function Home() {
  const featured = useResource(
    ({ signal }) => api.get("/api/products", { sort: "popular", limit: 8 }, { signal }),
    [],
  );
  const categories = useResource(({ signal }) => api.get("/api/catalog/categories", null, { signal }), []);
  const collections = useResource(({ signal }) => api.get("/api/catalog/collections", null, { signal }), []);
  const brands = useResource(({ signal }) => api.get("/api/brands", null, { signal }), []);
  const status = useResource(({ signal }) => api.get("/api/status", null, { signal }), []);

  const products = listOf(featured.data, "products");

  return (
    <>
      <section className="hero">
        <div>
          <h1 className="hero-title">Kit that outlives the season</h1>
          <p className="hero-body">
            {storeName()} sells outdoor and household goods chosen for one thing: they can be
            mended. Every item ships with the spares list and the repair guide.
          </p>
          <p className="row gap">
            <Link className="btn btn-primary" to="/catalog" data-track="hero-catalogue">
              Browse the catalogue
            </Link>
            <Link className="btn btn-quiet" to="/pages/repairs">
              How repairs work
            </Link>
          </p>
        </div>
        <div className="hero-panel">
          <p className="hero-panel-title">Delivery</p>
          <p className="muted small">
            Free over £60 · Next day before 14:00 · Collect from any of our shops
          </p>
          {status.data?.version ? (
            <p className="muted small">Catalogue build {String(status.data.version)}</p>
          ) : null}
        </div>
      </section>

      <Section
        title="Popular this week"
        description="What people are actually buying, refreshed every morning."
        actions={<Link className="btn btn-quiet" to="/catalog">See all</Link>}
      >
        {featured.loading ? <Loading label="Loading products…" /> : null}
        <ErrorNote error={featured.error} title="The product list did not load" onRetry={featured.reload} />
        <div className="grid product-grid">
          {products.map((product) => (
            <ProductCard key={product.id ?? product.slug} product={product} />
          ))}
        </div>
      </Section>

      <Section title="Departments">
        {categories.loading ? <Loading rows={1} /> : null}
        <ErrorNote error={categories.error} title="Departments did not load" onRetry={categories.reload} />
        <ul className="chips">
          {listOf(categories.data, "categories").map((category) => (
            <li key={category.id ?? category.slug}>
              <Link className="chip" to={`/catalog/${encodeURIComponent(category.slug ?? category.id)}`}>
                {category.title ?? category.name}
                {category.product_count ? <span className="chip-count">{category.product_count}</span> : null}
              </Link>
            </li>
          ))}
        </ul>
      </Section>

      <Section title="Collections" description="Grouped by the job, not by the department.">
        {collections.loading ? <Loading rows={1} /> : null}
        <ErrorNote error={collections.error} title="Collections did not load" onRetry={collections.reload} />
        <div className="grid collection-grid">
          {listOf(collections.data, "collections").map((collection) => (
            <Link
              key={collection.id ?? collection.slug}
              className="card collection-card"
              to={`/search?q=${encodeURIComponent(collection.title ?? collection.name ?? "")}`}
            >
              <span className="collection-title">{collection.title ?? collection.name}</span>
              {collection.description ? (
                <span className="muted small">{collection.description}</span>
              ) : null}
            </Link>
          ))}
        </div>
      </Section>

      <Section title="Makers we buy from">
        <ErrorNote error={brands.error} title="The maker list did not load" onRetry={brands.reload} />
        <ul className="chips">
          {listOf(brands.data, "brands").map((brand) => (
            <li key={brand.id ?? brand.slug}>
              <Link className="chip" to={`/search?q=${encodeURIComponent(brand.name ?? "")}`}>
                {brand.name}
              </Link>
            </li>
          ))}
        </ul>
      </Section>
    </>
  );
}
