import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";

import Stars from "../components/Stars.jsx";
import { ErrorNote, Loading, Notice, Section } from "../components/ui.jsx";
import { api, useAction, useResource } from "../lib/api.js";
import { useCart } from "../lib/cart.jsx";
import { useSession } from "../lib/session.jsx";
import { formatDate, listOf, money } from "../lib/store.js";

function ReviewForm({ productId, onPosted }) {
  const [form, setForm] = useState({ rating: 5, title: "", body: "" });
  const { pending, error, run } = useAction();
  const [done, setDone] = useState(false);

  const submit = async (event) => {
    event.preventDefault();
    const result = await run(() =>
      api.post(`/api/products/${encodeURIComponent(productId)}/reviews`, {
        rating: Number(form.rating),
        title: form.title,
        body: form.body,
      }),
    );
    if (result !== undefined) {
      setDone(true);
      setForm({ rating: 5, title: "", body: "" });
      onPosted?.();
    }
  };

  return (
    <form className="card stack" onSubmit={submit}>
      <h3 className="card-title">Write a review</h3>
      {done ? <Notice tone="good">Thank you — your review is with the moderators.</Notice> : null}
      <label className="field-label" htmlFor="review-rating">Rating</label>
      <select
        id="review-rating"
        className="input"
        value={form.rating}
        onChange={(event) => setForm({ ...form, rating: event.target.value })}
      >
        {[5, 4, 3, 2, 1].map((value) => (
          <option key={value} value={value}>{value} out of 5</option>
        ))}
      </select>
      <label className="field-label" htmlFor="review-title">Headline</label>
      <input
        id="review-title"
        className="input"
        value={form.title}
        maxLength={120}
        onChange={(event) => setForm({ ...form, title: event.target.value })}
        required
      />
      <label className="field-label" htmlFor="review-body">Your review</label>
      <textarea
        id="review-body"
        className="input"
        rows={5}
        value={form.body}
        onChange={(event) => setForm({ ...form, body: event.target.value })}
        required
      />
      <ErrorNote error={error} title="The review was not saved" />
      <button type="submit" className="btn btn-primary" disabled={pending} data-track="review-submit">
        {pending ? "Sending…" : "Post review"}
      </button>
    </form>
  );
}

export default function Product() {
  const { slug } = useParams();
  const { addItem } = useCart();
  const { signedIn } = useSession();
  const [variantId, setVariantId] = useState(null);
  const [quantity, setQuantity] = useState(1);
  const [added, setAdded] = useState(false);
  const basket = useAction();
  const wish = useAction();

  const product = useResource(
    ({ signal }) => api.get(`/api/products/${encodeURIComponent(slug)}`, null, { signal }),
    [slug],
  );
  const item = product.data?.product ?? product.data;
  const productId = item?.id;

  const reviews = useResource(
    ({ signal }) => api.get(`/api/products/${encodeURIComponent(productId)}/reviews`, null, { signal }),
    [productId],
    { skip: !productId },
  );
  const shipping = useResource(({ signal }) => api.get("/api/shipping/methods", null, { signal }), []);

  const variants = useMemo(() => listOf(item, "variants", "options"), [item]);
  useEffect(() => {
    if (variants.length && !variants.some((v) => v.id === variantId)) setVariantId(variants[0].id);
  }, [variants, variantId]);

  const variant = variants.find((v) => v.id === variantId) ?? variants[0] ?? null;
  const price = variant?.price_cents ?? item?.price_cents ?? item?.min_price_cents;

  const add = () =>
    basket.run(async () => {
      await addItem({
        variant_id: variant?.id ?? item?.default_variant_id ?? item?.id,
        quantity: Number(quantity) || 1,
        unit_price_cents: price,
      });
      setAdded(true);
    });

  const saveForLater = () =>
    wish.run(() =>
      api.post("/api/account/wishlist/items", { product_id: productId, variant_id: variant?.id ?? null }),
    );

  if (product.loading) return <Loading label="Loading the product…" />;
  if (product.error) return <ErrorNote error={product.error} title="This product did not load" onRetry={product.reload} />;
  if (!item) return <p className="muted">We could not find that product.</p>;

  const reviewList = listOf(reviews.data, "reviews");

  return (
    <>
      <nav className="crumbs" aria-label="Breadcrumb">
        <Link to="/catalog">Catalogue</Link>
        {item.category ? (
          <>
            <span aria-hidden="true"> / </span>
            <Link to={`/catalog/${encodeURIComponent(item.category.slug ?? item.category)}`}>
              {item.category.title ?? item.category.name ?? item.category}
            </Link>
          </>
        ) : null}
        <span aria-hidden="true"> / </span>
        <span>{item.title ?? item.name}</span>
      </nav>

      <div className="product-detail">
        <div className="product-media">
          {item.image_url ? (
            <img src={item.image_url} alt={item.title ?? item.name} />
          ) : (
            <span className="thumb-mark large" aria-hidden="true" />
          )}
        </div>

        <div className="product-buy">
          <h1 className="product-title">{item.title ?? item.name}</h1>
          {item.brand ? <p className="muted">{item.brand?.name ?? item.brand}</p> : null}
          <Stars value={item.rating ?? item.rating_average} count={item.review_count} />
          <p className="price-large">{money(price, item.currency)}</p>
          {item.description ? <p className="product-desc">{item.description}</p> : null}

          {variants.length > 1 ? (
            <>
              <label className="field-label" htmlFor="variant">Option</label>
              <select
                id="variant"
                className="input"
                value={variantId ?? ""}
                onChange={(event) => setVariantId(event.target.value)}
              >
                {variants.map((option) => (
                  <option key={option.id} value={option.id}>
                    {option.title ?? option.name ?? option.sku} — {money(option.price_cents, item.currency)}
                    {option.in_stock === false ? " (out of stock)" : ""}
                  </option>
                ))}
              </select>
            </>
          ) : null}

          <label className="field-label" htmlFor="quantity">Quantity</label>
          <input
            id="quantity"
            className="input narrow"
            type="number"
            min={1}
            max={20}
            value={quantity}
            onChange={(event) => setQuantity(event.target.value)}
          />

          <div className="row gap">
            <button type="button" className="btn btn-primary" onClick={add} disabled={basket.pending} data-track="add-to-basket">
              {basket.pending ? "Adding…" : "Add to basket"}
            </button>
            {signedIn ? (
              <button type="button" className="btn btn-quiet" onClick={saveForLater} disabled={wish.pending}>
                Save for later
              </button>
            ) : (
              <Link className="btn btn-quiet" to="/sign-in">Sign in to save</Link>
            )}
          </div>
          <ErrorNote error={basket.error} title="We could not add that" />
          <ErrorNote error={wish.error} title="We could not save that" />
          {added ? (
            <Notice tone="good">
              Added to your basket. <Link to="/cart">Go to basket</Link>
            </Notice>
          ) : null}

          <ul className="plain facts">
            {item.sku ? <li><span className="muted">Item code</span> {item.sku}</li> : null}
            {item.material ? <li><span className="muted">Material</span> {item.material}</li> : null}
            {item.weight_grams ? <li><span className="muted">Weight</span> {item.weight_grams} g</li> : null}
            {listOf(shipping.data, "methods").slice(0, 2).map((method) => (
              <li key={method.id ?? method.code}>
                <span className="muted">{method.title ?? method.name}</span> {money(method.rate_cents)}
              </li>
            ))}
          </ul>
        </div>
      </div>

      <Section title={`Reviews${reviewList.length ? ` (${reviewList.length})` : ""}`}>
        {reviews.loading ? <Loading rows={2} /> : null}
        <ErrorNote error={reviews.error} title="Reviews did not load" onRetry={reviews.reload} />
        <div className="reviews">
          {reviewList.map((review) => (
            <article className="card review" key={review.id}>
              <header className="review-head">
                <Stars value={review.rating} />
                <span className="muted small">
                  {review.author_name ?? review.author ?? "Customer"} · {formatDate(review.created_at)}
                </span>
              </header>
              <h3 className="review-title">{review.title}</h3>
              {/* Review text is customer writing; it goes on the page as text. */}
              <p className="review-body">{review.body ?? review.text ?? ""}</p>
            </article>
          ))}
          {!reviews.loading && reviewList.length === 0 ? (
            <p className="muted">No reviews yet. Be the first.</p>
          ) : null}
        </div>
        {signedIn ? (
          <ReviewForm productId={productId} onPosted={reviews.reload} />
        ) : (
          <p className="muted">
            <Link to="/sign-in">Sign in</Link> to write a review.
          </p>
        )}
      </Section>
    </>
  );
}
