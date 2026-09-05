import { TELEMETRY_REQUEST_STATE, type RequestState, type TelemetryClient } from "./client.js";
import {
  clientIp,
  collectAttributes,
  headerValue,
  isSynthetic,
  type RequestLike,
} from "./request.js";
import { composeRoute, watchRoute } from "./route.js";
import type { HttpRequestEvent } from "./types.js";

export interface MiddlewareOptions {
  /** Client to report to. Defaults to the process-wide one. */
  client?: TelemetryClient;
  /**
   * Resolve the authenticated principal. The default probes the usual places
   * (`req.auth.subject`, `req.user.id`, `req.session.userId`), which is what per-tenant
   * dashboards group by.
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
 * Express 5 middleware recording one request event per request.
 *
 * Mount it first, ahead of body parsers and routers: the route accessor has to be in
 * place before routing happens. Attributes are still collected from parsed bodies,
 * because that work runs at the end of the request rather than here.
 *
 * Cost on the response path is kept as near to nothing as it can be — install an accessor,
 * wrap `res.end`, register a listener. Hashing and flattening happen after the response
 * bytes have already been handed to the socket, so the endpoint's own latency numbers
 * stay honest.
 *
 * The middleware sets no response header, adds no route, writes no log line and touches
 * no error body. Instrumentation that changes what a client sees is instrumentation
 * that changes what it measures.
 */
export function telemetryMiddleware(options: MiddlewareOptions = {}) {
  const identify = options.identify ?? defaultIdentify;
  const ignore = options.ignore;

  return function requestTelemetry(req: unknown, res: unknown, next: Next): void {
    // next() is called exactly once, outside every try block. It runs the rest of the
    // chain synchronously, so wrapping it would both swallow the application's own
    // errors and risk a second next() from the catch clause.
    let client: TelemetryClient | null = null;
    try {
      const candidate = options.client ?? getDefaultClient();
      if (candidate.enabled && !(ignore && ignore(req))) client = candidate;
    } catch {
      client = null;
    }

    if (client !== null) try {
      // Re-bound as a const so the closures below keep the non-null narrowing.
      const active = client;
      const request = req as RequestLike & Record<symbol, unknown>;
      const response = res as ResponseLike;
      const startedAt = Date.now() / 1000;

      const state: RequestState = { extraAttributes: [] };
      request[TELEMETRY_REQUEST_STATE] = state;

      const readRoute = watchRoute(request as never);
      let lateParams: Record<string, unknown> | null = null;
      let recorded = false;

      const record = (): void => {
        if (recorded) return;
        recorded = true;
        try {
          const snapshot = readRoute();
          const attributes = collectAttributes(
            request,
            lateParams ?? snapshot?.params ?? {},
            active.config,
          );
          if (state.extraAttributes.length > 0) attributes.push(...state.extraAttributes);

          const event: HttpRequestEvent = {
            type: "http_request",
            app: active.config.service,
            ts: startedAt,
            method: (request.method ?? "GET").toUpperCase(),
            route: composeRoute(snapshot),
            path: pathOf(request),
            status: response.statusCode ?? 0,
            auth_subject: identify(req) ?? null,
            params: attributes,
          };
          const ip = clientIp(request);
          if (ip) event.client_ip = ip;
          const ua = headerValue(request, "user-agent");
          if (ua) event.user_agent = ua;
          if (isSynthetic(request, active.config)) event.synthetic = true;

          active.emit(event);
        } catch {
          // Never propagate, never log. A stack trace on stdout would put telemetry
          // noise into the application's own logs, where it does not belong.
        }
      };

      // `req.params` is restored by the router as it unwinds, so it is already gone by
      // the time `finish` fires. Copy it from inside the handler frame, but only after
      // the original end() has flushed, so nothing is added to the response time.
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

      // `close` covers client aborts, where `finish` never fires; the `recorded` guard
      // keeps it to exactly one event either way.
      response.once?.("finish", record);
      response.once?.("close", record);
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

// Late binding to avoid a module cycle: index.ts owns the process-wide instance.
let defaultClientGetter: (() => TelemetryClient) | null = null;

/** @internal wired by index.ts */
export function setDefaultClientGetter(getter: () => TelemetryClient): void {
  defaultClientGetter = getter;
}

function getDefaultClient(): TelemetryClient {
  if (!defaultClientGetter) throw new Error("telemetry client not initialised");
  return defaultClientGetter();
}
