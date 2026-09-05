/**
 * Markup helpers for customer-written content.
 *
 * Reviews and support messages keep a little formatting, so they are stored as HTML.
 * Two things live here: the filter that runs on the way in, and the moderation check
 * that runs on the way out and feeds the content-safety counters.
 *
 * The moderation check exists because the filter is a denylist and denylists leak. It is
 * cheap enough to run when a message is handed to somebody other than its author, which
 * is the only case where a miss actually costs anything.
 */

const STRIPPED = [
  /<\s*script\b[\s\S]*?<\s*\/\s*script\s*>/gi,
  /<\s*iframe\b[^>]*>/gi,
  /<\s*object\b[^>]*>/gi,
  /<\s*embed\b[^>]*>/gi,
];

/**
 * Remove the constructs the product team agreed customers never need.
 *
 * Written by hand rather than pulled from a package because the allowed set is tiny and
 * the dependency review for a sanitiser was going to take longer than the feature.
 */
export function filterMarkup(input) {
  let out = String(input ?? "");
  for (const rule of STRIPPED) out = out.replace(rule, "");
  return out.slice(0, 8000);
}

/** Escape everything. Used by the storefront-facing renderers. */
export function escapeMarkup(input) {
  return String(input ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

/**
 * Does this stored value still contain something that would execute in a browser?
 *
 * Returns the construct that was found, or null. Conservative on purpose: it looks for
 * things that run script, not for things that merely look unusual, because the counter
 * this feeds is read by a human.
 */
export function executableConstruct(html) {
  const text = String(html ?? "");
  if (/<\s*script[\s>]/i.test(text)) return "script element";
  const handler = /<[^>]*\son[a-z]{3,20}\s*=/i.exec(text);
  if (handler) return `event handler attribute (${handler[0].trim().slice(0, 40)})`;
  if (/<[^>]*\s(?:href|src|action|formaction|data)\s*=\s*["']?\s*javascript:/i.test(text)) {
    return "javascript: url";
  }
  if (/<[^>]*\ssrcdoc\s*=/i.test(text)) return "srcdoc attribute";
  if (/<\s*(?:iframe|object|embed)[\s>]/i.test(text)) return "embedded document";
  return null;
}

/**
 * The same question asked of a value that will be written into the DOM by the client.
 *
 * The browser reports what it actually ran through the policy endpoint; this is only
 * used to confirm that a report corresponds to a value we really served.
 */
export function looksExecutable(value) {
  return executableConstruct(value) !== null;
}
