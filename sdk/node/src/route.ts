import { UNMATCHED_ROUTE } from "./types.js";

/** Snapshot of the routing state, taken while the router frame is still live. */
export interface RouteSnapshot {
  /** `req.route.path` as registered, e.g. `/:id`. */
  routePath: string | null;
  /** Accumulated mount prefix, e.g. `/api/orders`. */
  baseUrl: string;
  /** `req.params` copied at match time. */
  params: Record<string, unknown>;
}

/** The minimal shape of an Express request this module touches. */
interface RoutableRequest {
  route?: unknown;
  baseUrl?: string;
  params?: Record<string, unknown>;
}

function pathOfRoute(route: unknown): string | null {
  if (!route || typeof route !== "object") return null;
  const path = (route as { path?: unknown }).path;
  if (typeof path === "string") return path;
  // `app.get(['/a', '/b'], h)` keeps the array. Reporting all alternatives is more
  // honest than guessing which one matched, and the scorer can still match one.
  if (Array.isArray(path)) return path.filter((p) => typeof p === "string").join("|") || null;
  // Express 5 dropped bare RegExp routes, but a custom router may still supply one.
  if (path instanceof RegExp) return path.source;
  return null;
}

function snapshotFrom(req: RoutableRequest): RouteSnapshot | null {
  const routePath = pathOfRoute(req.route);
  if (routePath === null) return null;
  return {
    routePath,
    baseUrl: typeof req.baseUrl === "string" ? req.baseUrl : "",
    params: req.params ? { ...req.params } : {},
  };
}

/**
 * Start watching a request for its matched route.
 *
 * Reading `req.route` from a `finish` listener does not work: the router restores
 * `req.baseUrl` and `req.params` as it unwinds, so by the time the response is
 * flushed the mount prefix and the path params are gone. Instead we intercept the
 * assignment itself. Express writes `req.route` twice per match — once in the router
 * loop (before `req.params` is merged) and once in `Route.dispatch` (after) — so the
 * last write is the authoritative one and simply overwrites the earlier snapshot.
 *
 * The accessor is an own property of a single request object: nothing about it is
 * observable from outside the process.
 *
 * @returns a getter for the best snapshot known so far.
 */
export function watchRoute(req: RoutableRequest): () => RouteSnapshot | null {
  let snapshot: RouteSnapshot | null = null;
  let current: unknown = req.route;

  try {
    Object.defineProperty(req, "route", {
      configurable: true,
      enumerable: true,
      get() {
        return current;
      },
      set(value: unknown) {
        current = value;
        const taken = snapshotFrom({ route: value, baseUrl: req.baseUrl, params: req.params });
        if (taken) snapshot = taken;
      },
    });
  } catch {
    // A frozen or exotic request object: fall back to reading it live. Worse for
    // nested mounts, but never fatal to the target.
  }

  return () => snapshot ?? snapshotFrom(req);
}

/**
 * Compose the reported route template.
 *
 * Catalog entrypoints are written as full templates (`/api/orders/:id`), so the mount
 * prefix has to be glued back onto the router-local path.
 */
export function composeRoute(snapshot: RouteSnapshot | null): string {
  if (!snapshot) return UNMATCHED_ROUTE;
  const base = snapshot.baseUrl.endsWith("/") ? snapshot.baseUrl.slice(0, -1) : snapshot.baseUrl;
  const routePath = snapshot.routePath ?? "";
  if (routePath === "/" || routePath === "") return base || "/";
  const joined = `${base}${routePath.startsWith("/") ? "" : "/"}${routePath}`;
  return joined || UNMATCHED_ROUTE;
}
