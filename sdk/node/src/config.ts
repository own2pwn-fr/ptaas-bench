/** Configuration resolution: explicit options first, environment second, defaults last. */

/** Options accepted by {@link initBench}. */
export interface BenchOptions {
  /** Target app key, as used in the catalog (`app: shopfront`). Defaults to `BENCH_APP`. */
  app?: string;
  /** Collector base URL, e.g. `http://collector:8900`. Defaults to `BENCH_COLLECTOR_URL`. */
  collectorUrl?: string;
  /**
   * Master switch. Defaults to `BENCH_ENABLED`, else true when an app key is known.
   * When false every entry point becomes a no-op that still never throws.
   */
  enabled?: boolean;
  /** Max events per POST /v1/events batch. The collector caps this at 500. */
  batchSize?: number;
  /** Background flush period in ms. */
  flushIntervalMs?: number;
  /** Max events held in memory. Beyond this the oldest are dropped and counted. */
  maxQueueSize?: number;
  /** Abort a collector POST after this long. Never blocks the target either way. */
  requestTimeoutMs?: number;
  /** Header marking platform traffic. Case-insensitive. */
  selftestHeader?: string;
  /** User-agent of the platform seeder; matching requests are flagged synthetic. */
  seederUserAgent?: string | RegExp;
  /** Max nesting depth walked when flattening a JSON body. */
  maxBodyDepth?: number;
  /** Max parameter observations per event, to bound work on pathological bodies. */
  maxParams?: number;
  /**
   * Emit a note when the bounded queue drops events. On by default: a silent drop
   * would look like a tool that never reached an endpoint, i.e. a scoring error.
   */
  reportDrops?: boolean;
  /** Injection point for tests; defaults to global fetch. */
  fetchImpl?: typeof fetch;
}

export interface ResolvedConfig {
  app: string;
  collectorUrl: string | null;
  enabled: boolean;
  batchSize: number;
  flushIntervalMs: number;
  maxQueueSize: number;
  requestTimeoutMs: number;
  selftestHeader: string;
  seederUserAgent: RegExp | null;
  maxBodyDepth: number;
  maxParams: number;
  reportDrops: boolean;
  fetchImpl: typeof fetch;
}

/** Collector hard limit on `events[]`; larger batches are rejected outright. */
const COLLECTOR_MAX_BATCH = 500;

function envFlag(raw: string | undefined): boolean | undefined {
  if (raw === undefined || raw === "") return undefined;
  const v = raw.trim().toLowerCase();
  if (v === "0" || v === "false" || v === "no" || v === "off") return false;
  if (v === "1" || v === "true" || v === "yes" || v === "on") return true;
  return undefined;
}

function toRegExp(value: string | RegExp | undefined): RegExp | null {
  if (value === undefined) return null;
  if (value instanceof RegExp) return value;
  if (value === "") return null;
  // Treated as a literal substring, not a pattern: operators would make an
  // accidental `.` in a UA string match far more traffic than intended.
  return new RegExp(value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "i");
}

function stripTrailingSlash(url: string): string {
  return url.endsWith("/") ? url.slice(0, -1) : url;
}

export function resolveConfig(options: BenchOptions = {}, env: NodeJS.ProcessEnv = process.env): ResolvedConfig {
  const app = options.app ?? env.BENCH_APP ?? "";
  const collectorUrlRaw = options.collectorUrl ?? env.BENCH_COLLECTOR_URL ?? "";
  const collectorUrl = collectorUrlRaw ? stripTrailingSlash(collectorUrlRaw) : null;
  // No app key means nobody configured the benchmark; staying off keeps the SDK
  // inert in the target's own dev/test environments.
  const enabled = options.enabled ?? envFlag(env.BENCH_ENABLED) ?? app !== "";

  return {
    app,
    collectorUrl,
    enabled: enabled && app !== "",
    batchSize: Math.min(options.batchSize ?? COLLECTOR_MAX_BATCH, COLLECTOR_MAX_BATCH),
    flushIntervalMs: options.flushIntervalMs ?? 250,
    maxQueueSize: options.maxQueueSize ?? 10_000,
    requestTimeoutMs: options.requestTimeoutMs ?? 2_000,
    selftestHeader: (options.selftestHeader ?? env.BENCH_SELFTEST_HEADER ?? "x-bench-selftest").toLowerCase(),
    seederUserAgent: toRegExp(options.seederUserAgent ?? env.BENCH_SEEDER_UA ?? "ptaas-bench-seeder"),
    maxBodyDepth: options.maxBodyDepth ?? 8,
    maxParams: options.maxParams ?? 512,
    reportDrops: options.reportDrops ?? true,
    fetchImpl: options.fetchImpl ?? globalThis.fetch,
  };
}
