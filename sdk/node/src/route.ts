import { UNMATCHED_ROUTE } from "./types.js";

/** Routing state, captured while the router frame is still live. */
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
  // `app.get(['/a', '/b'], h)` keeps the array. Reporting every alternative is more
  // honest than guessing which one matched.
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
 * Watch a request for the route it eventually matches.
 *
 * Reading `req.route` from a `finish` listener does not work. Express restores
 * `req.baseUrl` and `req.params` as the router unwinds, so once the response has been
 * flushed the mount prefix and the path parameters are already gone, and every mounted
 * router reports its bare local path. Intercepting the assignment avoids that. Express
 * writes `req.route` twice per match — once in the router loop, before `req.params` is
 * merged, and once in `Route.dispatch`, after — so the last write is the authoritative
 * one and simply replaces the earlier snapshot.
 *
 * The accessor is an own property of one request object and changes nothing an
 * application handler can observe: reading `req.route` still returns what Express put
 * there.
 *
 * @returns a getter for the best snapshot so far.
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
    // Frozen or exotic request object: fall back to reading it live. Less accurate for
    // nested mounts, never fatal.
  }

  return () => snapshot ?? snapshotFrom(req);
}

/**
 * Compose the reported template.
 *
 * Dashboards group by template, so the mount prefix has to be glued back onto the
 * router-local path — otherwise every mounted router collapses onto `/:id`.
 */
export function composeRoute(snapshot: RouteSnapshot | null): string {
  if (!snapshot) return UNMATCHED_ROUTE;
  const base = snapshot.baseUrl.endsWith("/") ? snapshot.baseUrl.slice(0, -1) : snapshot.baseUrl;
  const routePath = snapshot.routePath ?? "";
  if (routePath === "/" || routePath === "") return base || "/";
  const joined = `${base}${routePath.startsWith("/") ? "" : "/"}${routePath}`;
  return joined || UNMATCHED_ROUTE;
}
