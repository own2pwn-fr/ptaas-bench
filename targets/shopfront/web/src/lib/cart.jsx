/**
 * The basket.
 *
 * The basket lives on the server — it has to survive a device change and it is what the
 * checkout reads — so this context is a thin cache over the cart endpoints plus the one
 * piece of bookkeeping the UI needs: create the basket lazily, on the first line added.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

import { api } from "./api.js";
import { listOf } from "./store.js";

const CartContext = createContext(null);

export function CartProvider({ children }) {
  const [cart, setCart] = useState(null);
  const [summary, setSummary] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.get("/api/cart");
      setCart(data?.cart ?? data ?? null);
      setError(null);
    } catch (caught) {
      if (caught?.status === 404) setCart(null);
      else setError(caught);
    } finally {
      setLoading(false);
    }
  }, []);

  const loadSummary = useCallback(async () => {
    try {
      const data = await api.get("/api/cart/summary");
      setSummary(data?.summary ?? data ?? null);
    } catch {
      setSummary(null);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const ensure = useCallback(async () => {
    if (cart?.id) return cart;
    const data = await api.post("/api/cart", {});
    const created = data?.cart ?? data;
    setCart(created);
    return created;
  }, [cart]);

  const addItem = useCallback(
    async ({ variant_id, quantity = 1, unit_price_cents }) => {
      await ensure();
      await api.post("/api/cart/items", { variant_id, quantity, unit_price_cents });
      await load();
      await loadSummary();
    },
    [ensure, load, loadSummary],
  );

  const updateItem = useCallback(
    async (id, quantity) => {
      await api.patch(`/api/cart/items/${encodeURIComponent(id)}`, { quantity });
      await load();
      await loadSummary();
    },
    [load, loadSummary],
  );

  const removeItem = useCallback(
    async (id) => {
      await api.del(`/api/cart/items/${encodeURIComponent(id)}`);
      await load();
      await loadSummary();
    },
    [load, loadSummary],
  );

  const items = useMemo(() => listOf(cart, "items", "lines"), [cart]);
  const count = useMemo(
    () => items.reduce((total, line) => total + (Number(line.quantity) || 0), 0),
    [items],
  );

  const value = useMemo(
    () => ({ cart, items, count, summary, loading, error, reload: load, loadSummary, addItem, updateItem, removeItem }),
    [cart, items, count, summary, loading, error, load, loadSummary, addItem, updateItem, removeItem],
  );

  return <CartContext.Provider value={value}>{children}</CartContext.Provider>;
}

export function useCart() {
  const value = useContext(CartContext);
  if (!value) throw new Error("useCart must be used inside CartProvider");
  return value;
}
