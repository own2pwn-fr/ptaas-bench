/**
 * Shop identity and formatting.
 *
 * The document shell publishes `window.__STORE__` before the bundle runs: the trading
 * name, the currency and the locale of the estate the browser is talking to. Nothing in
 * the client hardcodes those — a second estate of the same release has a different name.
 */
const fallback = { name: "Storefront", currency: "EUR", locale: "en-GB" };

export function storeInfo() {
  const raw = typeof window === "undefined" ? null : window.__STORE__;
  if (!raw || typeof raw !== "object") return fallback;
  return {
    name: typeof raw.name === "string" && raw.name ? raw.name : fallback.name,
    currency: typeof raw.currency === "string" && raw.currency ? raw.currency : fallback.currency,
    locale: typeof raw.locale === "string" && raw.locale ? raw.locale : fallback.locale,
  };
}

export function storeName() {
  return storeInfo().name;
}

/** Prices travel as integer minor units; never as floats. */
export function money(cents, currency) {
  const info = storeInfo();
  const amount = Number.isFinite(Number(cents)) ? Number(cents) : 0;
  try {
    return new Intl.NumberFormat(info.locale, {
      style: "currency",
      currency: currency || info.currency,
    }).format(amount / 100);
  } catch {
    return `${(amount / 100).toFixed(2)} ${currency || info.currency}`;
  }
}

export function formatDate(value, options) {
  if (!value) return "—";
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  try {
    return new Intl.DateTimeFormat(storeInfo().locale, options ?? { dateStyle: "medium" }).format(date);
  } catch {
    return date.toISOString().slice(0, 10);
  }
}

export function formatDateTime(value) {
  return formatDate(value, { dateStyle: "medium", timeStyle: "short" });
}

export function readCookie(name) {
  if (typeof document === "undefined") return null;
  const match = document.cookie.match(new RegExp(`(?:^|;\\s*)${name}=([^;]*)`));
  return match ? decodeURIComponent(match[1]) : null;
}

export function writeCookie(name, value, days = 365) {
  if (typeof document === "undefined") return;
  const expires = new Date(Date.now() + days * 86400000).toUTCString();
  document.cookie = `${name}=${encodeURIComponent(value)}; path=/; expires=${expires}; samesite=lax`;
}

/** Title-case a machine status such as `awaiting_dispatch`. */
export function humanise(value) {
  if (!value) return "—";
  const text = String(value).replace(/[_-]+/g, " ").trim();
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** Pull a list out of a response that may be `{items:[]}`, `{orders:[]}` or a bare array. */
export function listOf(source, ...keys) {
  if (Array.isArray(source)) return source;
  if (!source || typeof source !== "object") return [];
  for (const key of [...keys, "items", "results", "data", "records"]) {
    if (Array.isArray(source[key])) return source[key];
  }
  return [];
}
