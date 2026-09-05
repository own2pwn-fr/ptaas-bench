/**
 * `@internal/telemetry` — request instrumentation and application signals.
 *
 * Two jobs and nothing else: record what each request carried, and record the counters
 * the application itself decides are worth raising. Interpretation lives in the
 * backend; this package stays dumb so that it can be read in one sitting and trusted on
 * the hot path.
 *
 * ```ts
 * import { initTelemetry, telemetryMiddleware, telemetry } from "@internal/telemetry";
 *
 * initTelemetry();                  // reads TELEMETRY_SERVICE / TELEMETRY_ENDPOINT
 * app.use(telemetryMiddleware());   // before the body parsers and routers
 * ```
 */
import { TelemetryClient } from "./client.js";
import type { TelemetryOptions } from "./config.js";
import { setDefaultClientGetter } from "./middleware.js";

export { TelemetryClient, TELEMETRY_REQUEST_STATE } from "./client.js";
export type {
  EgressDeclaration,
  GraphQLOperation,
  SignalOptions,
  WebSocketFrame,
} from "./client.js";
export type { ResolvedConfig, TelemetryOptions } from "./config.js";
export { telemetryMiddleware } from "./middleware.js";
export type { MiddlewareOptions } from "./middleware.js";
export { observe, rawValue, sha256, truncateSample, SAMPLE_MAX_CHARS } from "./attributes.js";
export { compileSourceMatcher, normaliseAddress } from "./net.js";
export type { SourceMatcher } from "./net.js";
export { composeRoute, watchRoute } from "./route.js";
export type { RouteSnapshot } from "./route.js";
export type { TransportStats } from "./transport.js";
export { SIGNAL_NAME_PATTERN, UNMATCHED_ROUTE } from "./types.js";
export type {
  Attribute,
  AttributeSource,
  EgressCorrelation,
  EventBase,
  HttpRequestEvent,
  NoteEvent,
  OobEvent,
  SignalEvent,
  TelemetryEvent,
} from "./types.js";

let singleton: TelemetryClient | null = null;

/**
 * Create, or replace, the process-wide telemetry client.
 *
 * Everything is optional: with no arguments the environment is read
 * (`TELEMETRY_SERVICE`, `TELEMETRY_ENDPOINT`, `TELEMETRY_ENABLED`). With no service
 * name the client stays inert and every entry point is a no-op, so an application runs
 * unchanged without a collector.
 */
export function initTelemetry(options: TelemetryOptions = {}): TelemetryClient {
  const previous = singleton;
  singleton = new TelemetryClient(options);
  // Fire-and-forget: replacing the client must not make the caller await a flush.
  if (previous) void previous.shutdown().catch(() => undefined);
  return singleton;
}

/** The process-wide client, created from the environment on first use. */
export function getTelemetry(): TelemetryClient {
  if (!singleton) singleton = new TelemetryClient();
  return singleton;
}

setDefaultClientGetter(getTelemetry);

/**
 * Convenience facade over {@link getTelemetry}.
 *
 * Application code reads as `telemetry.signal("shop.catalog.query.plan_anomaly", …)`:
 * one line at the point of interest, no plumbing.
 */
export const telemetry = {
  get service(): string {
    return getTelemetry().service;
  },
  get enabled(): boolean {
    return getTelemetry().enabled;
  },
  signal: (...args: Parameters<TelemetryClient["signal"]>): void => getTelemetry().signal(...args),
  note: (...args: Parameters<TelemetryClient["note"]>): void => getTelemetry().note(...args),
  correlate: (...args: Parameters<TelemetryClient["correlate"]>): string =>
    getTelemetry().correlate(...args),
  graphql: (...args: Parameters<TelemetryClient["graphql"]>): void => getTelemetry().graphql(...args),
  websocket: (...args: Parameters<TelemetryClient["websocket"]>): void =>
    getTelemetry().websocket(...args),
  emit: (...args: Parameters<TelemetryClient["emit"]>): void => getTelemetry().emit(...args),
  flush: (): Promise<void> => getTelemetry().flush(),
  stats: (): ReturnType<TelemetryClient["stats"]> => getTelemetry().stats(),
  shutdown: (): Promise<void> => getTelemetry().shutdown(),
};
