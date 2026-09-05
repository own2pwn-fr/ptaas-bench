import { compileSourceMatcher, type SourceMatcher } from "./net.js";

/** Options accepted by the telemetry client. */
export interface TelemetryOptions {
  /** Service name reported with every event. Defaults to `TELEMETRY_SERVICE`. */
  service?: string;
  /** Collector base URL, e.g. `http://otel-collector:8900`. Defaults to `TELEMETRY_ENDPOINT`. */
  endpoint?: string;
  /**
   * Master switch. Defaults to `TELEMETRY_ENABLED`, else on when a service name is
   * known. When off, every entry point is a no-op that still never throws.
   */
  enabled?: boolean;
  /** Path the batched events are POSTed to. Defaults to `TELEMETRY_EVENTS_PATH`. */
  eventsPath?: string;
  /** Path egress correlations are POSTed to. */
  correlationsPath?: string;
  /** Max events per POST. The ingest endpoint caps a batch at 500. */
  batchSize?: number;
  /** Background flush period in ms. */
  flushIntervalMs?: number;
  /** Max events held in memory. Beyond this the oldest are discarded and counted. */
  maxQueueSize?: number;
  /** Abort a collector POST after this long. Never blocks the service either way. */
  requestTimeoutMs?: number;
  /**
   * Source ranges belonging to the platform's synthetic monitoring probes, as CIDR
   * prefixes. Defaults to `TELEMETRY_SYNTHETIC_CIDRS` (comma or space separated).
   */
  syntheticCidrs?: readonly string[];
  /** Max nesting depth walked when flattening a JSON body. */
  maxBodyDepth?: number;
  /** Max attributes recorded per event, to bound work on pathological bodies. */
  maxAttributes?: number;
  /**
   * Report discarded events as a note. On by default: a queue overflow that nobody
   * hears about looks exactly like an endpoint that stopped receiving traffic.
   */
  reportDiscards?: boolean;
  /** Injection point for tests; defaults to global fetch. */
  fetchImpl?: typeof fetch;
}

export interface ResolvedConfig {
  service: string;
  endpoint: string | null;
  enabled: boolean;
  eventsPath: string;
  correlationsPath: string;
  batchSize: number;
  flushIntervalMs: number;
  maxQueueSize: number;
  requestTimeoutMs: number;
  syntheticSources: SourceMatcher;
  maxBodyDepth: number;
  maxAttributes: number;
  reportDiscards: boolean;
  fetchImpl: typeof fetch;
}

/** Ingest limit on `events[]`; a larger batch is rejected outright. */
const MAX_BATCH = 500;

function envBool(raw: string | undefined): boolean | undefined {
  if (raw === undefined || raw === "") return undefined;
  const v = raw.trim().toLowerCase();
  if (v === "0" || v === "false" || v === "no" || v === "off") return false;
  if (v === "1" || v === "true" || v === "yes" || v === "on") return true;
  return undefined;
}

function envList(raw: string | undefined): string[] {
  if (!raw) return [];
  return raw.split(/[,\s]+/).filter(Boolean);
}

function stripTrailingSlash(url: string): string {
  return url.endsWith("/") ? url.slice(0, -1) : url;
}

function withLeadingSlash(path: string): string {
  return path.startsWith("/") ? path : `/${path}`;
}

export function resolveConfig(
  options: TelemetryOptions = {},
  env: NodeJS.ProcessEnv = process.env,
): ResolvedConfig {
  const service = options.service ?? env.TELEMETRY_SERVICE ?? "";
  const endpointRaw = options.endpoint ?? env.TELEMETRY_ENDPOINT ?? "";
  const endpoint = endpointRaw ? stripTrailingSlash(endpointRaw) : null;
  // With no service name nothing is configured, so the client stays inert. That keeps
  // it silent in local development and in unit tests without any extra wiring.
  const enabled = options.enabled ?? envBool(env.TELEMETRY_ENABLED) ?? service !== "";

  return {
    service,
    endpoint,
    enabled: enabled && service !== "",
    eventsPath: withLeadingSlash(options.eventsPath ?? env.TELEMETRY_EVENTS_PATH ?? "/v1/traces"),
    correlationsPath: withLeadingSlash(
      options.correlationsPath ?? env.TELEMETRY_CORRELATIONS_PATH ?? "/v1/correlations",
    ),
    batchSize: Math.min(options.batchSize ?? MAX_BATCH, MAX_BATCH),
    flushIntervalMs: options.flushIntervalMs ?? 250,
    maxQueueSize: options.maxQueueSize ?? 10_000,
    requestTimeoutMs: options.requestTimeoutMs ?? 2_000,
    syntheticSources: compileSourceMatcher(
      options.syntheticCidrs ?? envList(env.TELEMETRY_SYNTHETIC_CIDRS),
    ),
    maxBodyDepth: options.maxBodyDepth ?? 8,
    maxAttributes: options.maxAttributes ?? 512,
    reportDiscards: options.reportDiscards ?? true,
    fetchImpl: options.fetchImpl ?? globalThis.fetch,
  };
}
