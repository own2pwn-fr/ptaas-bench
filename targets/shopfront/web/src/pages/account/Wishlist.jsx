import { Link } from "react-router-dom";

import { ErrorNote, Loading, Section } from "../../components/ui.jsx";
import { api, useAction, useResource } from "../../lib/api.js";
import { useCart } from "../../lib/cart.jsx";
import { listOf, money } from "../../lib/store.js";

export default function Wishlist() {
  const wishlist = useResource(({ signal }) => api.get("/api/account/wishlist", null, { signal }), []);
  const { addItem } = useCart();
  const action = useAction();

  const items = listOf(wishlist.data, "items", "wishlist");

  const remove = (id) =>
    action.run(async () => {
      await api.del(`/api/account/wishlist/items/${encodeURIComponent(id)}`);
      wishlist.reload();
    });

  const moveToBasket = (item) =>
    action.run(async () => {
      await addItem({
        variant_id: item.variant_id ?? item.product_id ?? item.id,
        quantity: 1,
        unit_price_cents: item.price_cents,
      });
      await api.del(`/api/account/wishlist/items/${encodeURIComponent(item.id)}`);
      wishlist.reload();
    });

  return (
    <Section title="Saved items" description="Kept until you want them. We tell you if the price drops.">
      {wishlist.loading ? <Loading rows={2} /> : null}
      <ErrorNote error={wishlist.error} title="Your saved items did not load" onRetry={wishlist.reload} />
      <ErrorNote error={action.error} title="That did not work" />
      <ul className="plain card-list">
        {items.map((item) => (
          <li className="card payment-row" key={item.id}>
            <span>
              {item.product_slug ? (
                <Link to={`/product/${encodeURIComponent(item.product_slug)}`}>
                  {item.title ?? item.product_title}
                </Link>
              ) : (
                (item.title ?? item.product_title ?? "Item")
              )}
              {item.variant_title ? <span className="muted small"> · {item.variant_title}</span> : null}
              <span className="muted small"> · {money(item.price_cents)}</span>
            </span>
            <span className="row gap">
              <button type="button" className="linklike" onClick={() => moveToBasket(item)} disabled={action.pending}>
                Move to basket
              </button>
              <button type="button" className="linklike" onClick={() => remove(item.id)} disabled={action.pending}>
                Remove
              </button>
            </span>
          </li>
        ))}
        {!wishlist.loading && items.length === 0 ? (
          <li className="muted">
            Nothing saved. <Link to="/catalog">Have a look round.</Link>
          </li>
        ) : null}
      </ul>
    </Section>
  );
}
