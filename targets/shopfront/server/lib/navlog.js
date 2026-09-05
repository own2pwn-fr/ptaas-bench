/**
 * Recent document renders.
 *
 * Kept in memory for a few minutes so that a policy report can be attributed to the page
 * and the account that produced it. A report arrives on its own connection with nothing
 * but a document URI, which is not enough to act on: two customers on the same page
 * produce identical reports, and the one that matters is the one whose stored content
 * caused it.
 */
const WINDOW_MS = 5 * 60 * 1000;
const MAX = 2048;

const renders = [];

export function recordRender({ ip, path: documentPath, subject }) {
  const now = Date.now();
  renders.push({ ip: String(ip ?? ""), path: String(documentPath ?? ""), subject: subject ?? null, at: now });
  while (renders.length > MAX || (renders.length > 0 && now - renders[0].at > WINDOW_MS)) {
    renders.shift();
  }
}

/** The most recent render of `documentPath` from `ip`, or null. */
export function lastRender(ip, documentPath) {
  const now = Date.now();
  for (let i = renders.length - 1; i >= 0; i -= 1) {
    const entry = renders[i];
    if (now - entry.at > WINDOW_MS) break;
    if (entry.ip !== String(ip ?? "")) continue;
    if (entry.path !== documentPath && !documentPath.startsWith(`${entry.path}/`)) continue;
    return entry;
  }
  return null;
}

export function clearRenders() {
  renders.length = 0;
}
