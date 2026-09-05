import { BENCH_REQUEST_STATE, type Bench, type RequestState } from "./client.js";
import { clientIp, collectParams, headerValue, isSynthetic, type RequestLike } from "./request.js";
import { composeRoute, watchRoute } from "./route.js";
import type { HttpRequestEvent } from "./types.js";

export interface MiddlewareOptions {
  /** Bench instance to report to. Defaults to the process-wide one. */
  bench?: Bench;
  /**
   * Resolve the authenticated principal. The default probes the usual suspects
   * (`req.auth.subject`, `req.user.id`, `req.session.userId`) because the BOLA-style
   * oracles need to know who the caller was.
   */
  identify?: (req: unknown) => string | null | undefined;
  /** Skip instrumentation entirely for some requests (health probes, static assets). */
  ignore?: (req: unknown) => boolean;
}

type Next = (err?: unknown) => void;

interface ResponseLike {
  statusCode?: number;
  end?: (...args: unknown[]) => unknown;
  once?: (event: string, listener: () => void) => unknown;
}

function defaultIdentify(req: unknown): string | null | undefined {
  const r = req as {
    auth?: { subject?: unknown; sub?: unknown };
    user?: { id?: unknown; username?: unknown; email?: unknown };
    session?: { userId?: unknown; user?: { id?: unknown } };
  };
  const candidates = [
    r?.auth?.subject,
    r?.auth?.sub,
    r?.user?.id,
    r?.user?.username,
    r?.user?.email,
    r?.session?.userId,
    r?.session?.user?.id,
  ];
  for (const candidate of candidates) {
    if (typeof candidate === "string" && candidate) return candidate;
    if (typeof candidate === "number") return String(candidate);
  }
  return null;
}

/**
 * Express 5 middleware emitting one `http_request` event per request.
 *
 * Mount it first, before body parsers and routers: the route snapshot has to be
 * installed before routing happens. Parameter enumeration still sees parsed bodies
 * because it runs at the *end* of the request, not here.
 *
 * Cost on the response path is deliberately close to nothing: install an accessor,
 * wrap `res.end`, register a listener. All the hashing and flattening happens after
 * the response bytes have already been handed to the socket, because the catalog
 * contains timing oracles and instrumentation that showed up in a latency measurement
 * would invalidate them.
 *
 * The middleware sets no response header, adds no route, writes no log line and
 * touches no error body: the tool under test must not be able to detect that the
 * target is instrumented, or it could learn to behave differently under benchmark.
 */
export function benchMiddleware(options: MiddlewareOptions = {}) {
  const identify = options.identify ?? defaultIdentify;
  const ignore = options.ignore;

  return function benchInstrumentation(req: unknown, res: unknown, next: Next): void {
    // next() is called exactly once, outside every try block. It runs the rest of the
    // chain synchronously, so wrapping it would both swallow the target's own errors
    // and risk a second next() from the catch clause.
    let bench: Bench | null = null;
    try {
      const candidate = options.bench ?? getDefaultBench();
      if (candidate.enabled && !(ignore && ignore(req))) bench = candidate;
    } catch {
      bench = null;
    }

    if (bench !== null) try {
      // Re-bound as a const so the closures below keep the non-null narrowing.
      const active = bench;
      const request = req as RequestLike & Record<symbol, unknown>;
      const response = res as ResponseLike;
      const startedAt = Date.now() / 1000;

      const state: RequestState = { extraParams: [] };
      request[BENCH_REQUEST_STATE] = state;

      const readRoute = watchRoute(request as never);
      let lateParams: Record<string, unknown> | null = null;
      let emitted = false;

      const emit = (): void => {
        if (emitted) return;
        emitted = true;
        try {
          const snapshot = readRoute();
          const params = collectParams(request, lateParams ?? snapshot?.params ?? {}, active.config);
          if (state.extraParams.length > 0) params.push(...state.extraParams);

          const event: HttpRequestEvent = {
            type: "http_request",
            app: active.config.app,
            ts: startedAt,
            method: (request.method ?? "GET").toUpperCase(),
            route: composeRoute(snapshot),
            path: pathOf(request),
            status: response.statusCode ?? 0,
            auth_subject: identify(req) ?? null,
            params,
          };
          const ip = clientIp(request);
          if (ip) event.client_ip = ip;
          const ua = headerValue(request, "user-agent");
          if (ua) event.user_agent = ua;
          if (isSynthetic(request, active.config)) event.synthetic = true;

          active.emit(event);
        } catch {
          // Never propagate, never log: a stack trace on stdout would tell an
          // operator (and any log-scraping tool) that the target is instrumented.
        }
      };

      // `req.params` is restored by the router as it unwinds, so it is already gone
      // by the time `finish` fires. Snapshot it from inside the handler frame, but
      // only *after* the original end() has flushed, so nothing is added to latency.
      const originalEnd = response.end;
      if (typeof originalEnd === "function") {
        response.end = function patchedEnd(this: unknown, ...args: unknown[]): unknown {
          const result = originalEnd.apply(this, args);
          try {
            if (lateParams === null && request.params) lateParams = { ...request.params };
          } catch {
            /* ignore */
          }
          return result;
        };
      }

      // `close` covers client aborts, where `finish` never fires; `emitted` keeps it
      // to exactly one event either way.
      response.once?.("finish", emit);
      response.once?.("close", emit);
    } catch {
      // Falling through to next() is the only acceptable failure mode.
    }

    return next();
  };
}

function pathOf(req: RequestLike): string {
  const raw = req.originalUrl ?? req.url ?? "";
  const q = raw.indexOf("?");
  return q < 0 ? raw : raw.slice(0, q);
}

// Late import to avoid a module cycle: index.ts owns the singleton.
let defaultBenchGetter: (() => Bench) | null = null;

/** @internal wired by index.ts */
export function setDefaultBenchGetter(getter: () => Bench): void {
  defaultBenchGetter = getter;
}

function getDefaultBench(): Bench {
  if (!defaultBenchGetter) throw new Error("bench not initialised");
  return defaultBenchGetter();
}
