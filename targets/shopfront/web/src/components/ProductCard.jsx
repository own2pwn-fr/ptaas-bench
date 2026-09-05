import { Link } from "react-router-dom";

import { money } from "../lib/store.js";
import Stars from "./Stars.jsx";

/** One tile in a grid. Kept dumb: every list page builds the same shape for it. */
export default function ProductCard({ product }) {
  const slug = product.slug ?? product.handle ?? product.id;
  const price = product.price_cents ?? product.min_price_cents ?? product.unit_price_cents;
  return (
    <article className="card product-card">
      <Link className="product-card-link" to={`/product/${encodeURIComponent(slug)}`} data-track="product-card">
        <span className="product-thumb" aria-hidden="true">
          {product.image_url ? <img src={product.image_url} alt="" loading="lazy" /> : <span className="thumb-mark" />}
        </span>
        <span className="product-card-body">
          <span className="product-name">{product.title ?? product.name}</span>
          {product.brand ? <span className="product-brand">{product.brand?.name ?? product.brand}</span> : null}
          <span className="product-price">{money(price, product.currency)}</span>
        </span>
      </Link>
      <div className="product-card-foot">
        <Stars value={product.rating ?? product.rating_average} count={product.review_count} />
        {product.in_stock === false ? <span className="muted small">Back in stock soon</span> : null}
      </div>
    </article>
  );
}
