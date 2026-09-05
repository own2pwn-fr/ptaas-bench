/**
 * `@ptaas-bench/sdk` — ground-truth instrumentation for ptaas-bench target apps.
 *
 * Two jobs, and nothing else:
 *   1. report what the tool under test actually put on the wire ({@link benchMiddleware});
 *   2. report when a planted vulnerability genuinely fired ({@link Bench.trigger}).
 *
 * All interpretation — did that count as reach? as exercise? — belongs to the scoring
 * engine, which is the only component allowed to read the catalog. Keeping the SDK
 * dumb is what lets a target be audited by reading it.
 *
 * ```ts
 * import { initBench, benchMiddleware, bench } from "@ptaas-bench/sdk";
 *
 * initBench();                 // reads BENCH_APP / BENCH_COLLECTOR_URL
 * app.use(benchMiddleware());  // before the body parsers and routers
 * ```
 */
import { Bench } from "./client.js";
import type { BenchOptions } from "./config.js";
import { setDefaultBenchGetter } from "./middleware.js";

export { Bench, BENCH_REQUEST_STATE } from "./client.js";
export type { GraphQLOperation, TriggerEvidence, WebSocketFrame } from "./client.js";
export type { BenchOptions, ResolvedConfig } from "./config.js";
export { benchMiddleware } from "./middleware.js";
export type { MiddlewareOptions } from "./middleware.js";
export { observe, rawValue, sha256, truncateSample, SAMPLE_MAX_CHARS } from "./params.js";
export { composeRoute, watchRoute } from "./route.js";
export type { RouteSnapshot } from "./route.js";
export type { TransportStats } from "./transport.js";
export { UNMATCHED_ROUTE, VULN_ID_PATTERN } from "./types.js";
export type {
  BenchEvent,
  EventBase,
  HttpRequestEvent,
  NoteEvent,
  OobEvent,
  OracleKind,
  ParamLocation,
  ParamObservation,
  TriggerEvent,
} from "./types.js";

let singleton: Bench | null = null;

/**
 * Create (or replace) the process-wide Bench instance.
 *
 * Everything is optional: with no arguments the SDK reads `BENCH_APP` and
 * `BENCH_COLLECTOR_URL` from the environment, which is how the compose stack wires
 * targets up. With no `BENCH_APP` the SDK stays disabled and every entry point is a
 * no-op, so a target still runs normally outside the benchmark.
 */
export function initBench(options: BenchOptions = {}): Bench {
  const previous = singleton;
  singleton = new Bench(options);
  // Fire-and-forget: replacing the instance must not make the caller await a flush.
  if (previous) void previous.shutdown().catch(() => undefined);
  return singleton;
}

/** The process-wide Bench instance, created from the environment on first use. */
export function getBench(): Bench {
  if (!singleton) singleton = new Bench();
  return singleton;
}

setDefaultBenchGetter(getBench);

/**
 * Convenience facade over {@link getBench}.
 *
 * Target code is meant to read as `bench.trigger("BENCH-SHOP-0001", …)` — one
 * greppable line inside the vulnerable sink, no plumbing.
 */
export const bench = {
  get app(): string {
    return getBench().app;
  },
  get enabled(): boolean {
    return getBench().enabled;
  },
  trigger: (...args: Parameters<Bench["trigger"]>): void => getBench().trigger(...args),
  note: (...args: Parameters<Bench["note"]>): void => getBench().note(...args),
  graphql: (...args: Parameters<Bench["graphql"]>): void => getBench().graphql(...args),
  websocket: (...args: Parameters<Bench["websocket"]>): void => getBench().websocket(...args),
  emit: (...args: Parameters<Bench["emit"]>): void => getBench().emit(...args),
  flush: (): Promise<void> => getBench().flush(),
  stats: (): ReturnType<Bench["stats"]> => getBench().stats(),
  shutdown: (): Promise<void> => getBench().shutdown(),
};
