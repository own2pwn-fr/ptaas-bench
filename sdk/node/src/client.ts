import { resolveConfig, type BenchOptions, type ResolvedConfig } from "./config.js";
import { flattenInto, observe, rawValue } from "./params.js";
import { Transport, type TransportStats } from "./transport.js";
import {
  VULN_ID_PATTERN,
  type BenchEvent,
  type HttpRequestEvent,
  type OracleKind,
  type ParamObservation,
  type TriggerEvent,
} from "./types.js";

/** Evidence accompanying a {@link Bench.trigger} call. */
export interface TriggerEvidence {
  /** How the sink decided the flaw fired. Should match the catalog's `oracle.kind`. */
  oracleKind?: OracleKind;
  /** The attacker-controlled input that reached the sink. */
  payload?: string;
  /** Why the oracle condition is genuinely met (what was observed, not what was sent). */
  detail?: string;
  /** Correlation id, when the target tracks one. */
  requestId?: string;
  /** Force the synthetic flag (self-tests exercising their own sinks). */
  synthetic?: boolean;
}

/** A GraphQL operation, as seen by the resolver layer. */
export interface GraphQLOperation {
  operationName?: string | null;
  query?: string | null;
  variables?: Record<string, unknown> | null;
  /** HTTP route the GraphQL endpoint is mounted on. Defaults to `/graphql`. */
  route?: string;
  method?: string;
  synthetic?: boolean;
}

/** One WebSocket frame observed by the target. */
export interface WebSocketFrame {
  /** Route template the socket is mounted on, e.g. `/ws/orders`. */
  route: string;
  /** Concrete path of the upgrade request, when known. */
  path?: string;
  /** Frame payload. Objects and JSON strings are flattened into dotted params. */
  message?: unknown;
  /** Logical message type, recorded as its own param when present. */
  messageType?: string;
  authSubject?: string | null;
  clientIp?: string;
  synthetic?: boolean;
}

const MAX_EVIDENCE_CHARS = 1024;

/**
 * Coerce and clamp an evidence field.
 *
 * Sinks are hand-written in target apps and will sometimes pass a non-string (a row,
 * an error object). Coercing here keeps the event JSON-serialisable: an unserialisable
 * value would otherwise be discovered only at flush time, where it would take a whole
 * batch of unrelated events down with it.
 */
function clampEvidence(value: unknown): string | undefined {
  if (value === undefined || value === null) return undefined;
  const text = typeof value === "string" ? value : rawValue(value);
  return text.length > MAX_EVIDENCE_CHARS ? text.slice(0, MAX_EVIDENCE_CHARS) : text;
}

/**
 * The instrumentation handle a target application talks to.
 *
 * Every public method is total: it swallows its own errors and returns `void`. A
 * planted sink calling {@link Bench.trigger} must never be able to fail the request it
 * is embedded in, or the benchmark would measure the SDK instead of the tool.
 */
export class Bench {
  readonly config: ResolvedConfig;
  private readonly transport: Transport;

  constructor(options: BenchOptions = {}, env: NodeJS.ProcessEnv = process.env) {
    this.config = resolveConfig(options, env);
    this.transport = new Transport(this.config);
  }

  get enabled(): boolean {
    return this.config.enabled;
  }

  get app(): string {
    return this.config.app;
  }

  /** Queue a pre-built event. Stamps `app` and `ts` when the caller omitted them. */
  emit(event: BenchEvent): void {
    if (!this.config.enabled) return;
    try {
      if (!event.app) event.app = this.config.app;
      if (event.ts === undefined) event.ts = Date.now() / 1000;
      this.transport.enqueue(event);
    } catch {
      // Unreachable in practice; kept because "instrumentation never throws" is a
      // property of the benchmark, not an aspiration.
    }
  }

  /**
   * Report that a planted vulnerability actually fired.
   *
   * Call this from inside the vulnerable sink, at the point where the catalog's
   * `oracle.condition` is genuinely satisfied — not where the payload merely arrived.
   * Reflecting a quote is `exercise`; reaching the sink with an effect is `trigger`.
   *
   * @example
   * ```ts
   * if (parsedSql.includes("UNION") && rows.some((r) => r.__table !== "products")) {
   *   bench.trigger("BENCH-SHOP-0001", { oracleKind: "sink", payload: q, detail: "..." });
   * }
   * ```
   */
  trigger(vulnId: string, evidence: TriggerEvidence = {}): void {
    if (!this.config.enabled) return;
    if (!VULN_ID_PATTERN.test(vulnId)) {
      // The collector enforces the same pattern and would reject the entire batch,
      // taking unrelated events with it. Degrade to a note instead.
      this.note(`ptaas-bench sdk: rejected trigger with malformed vuln id ${JSON.stringify(vulnId)}`);
      return;
    }
    const event: TriggerEvent = {
      type: "trigger",
      app: this.config.app,
      ts: Date.now() / 1000,
      vuln_id: vulnId,
      evidence: {
        payload: clampEvidence(evidence.payload),
        detail: clampEvidence(evidence.detail),
        request_id: clampEvidence(evidence.requestId),
      },
    };
    if (evidence.oracleKind) event.oracle_kind = evidence.oracleKind;
    if (evidence.synthetic) event.synthetic = true;
    this.emit(event);
  }

  /** Free-form annotation, e.g. a seeding step or a target-side state transition. */
  note(message: string, options: { synthetic?: boolean } = {}): void {
    this.emit({
      type: "note",
      app: this.config.app,
      ts: Date.now() / 1000,
      message,
      ...(options.synthetic ? { synthetic: true } : {}),
    });
  }

  /**
   * Report a GraphQL operation.
   *
   * A single HTTP POST to `/graphql` hides every field a tool actually reached, so the
   * operation name and each variable are reported as params with `in: "graphql"`.
   * Pass the Express request to fold them into that request's `http_request` event;
   * without it a standalone event is emitted.
   */
  graphql(operation: GraphQLOperation, req?: unknown): void {
    if (!this.config.enabled) return;
    const params: ParamObservation[] = [];
    if (operation.operationName) params.push(observe("operationName", "graphql", operation.operationName));
    if (operation.variables) {
      flattenInto(params, operation.variables, "graphql", "variables", {
        maxDepth: this.config.maxBodyDepth,
        maxParams: this.config.maxParams,
      });
    }

    if (req && attachParams(req, params)) return;

    this.emit({
      type: "http_request",
      app: this.config.app,
      ts: Date.now() / 1000,
      method: operation.method ?? "POST",
      route: operation.route ?? "/graphql",
      path: operation.route ?? "/graphql",
      params,
      ...(operation.synthetic ? { synthetic: true } : {}),
    } satisfies HttpRequestEvent);
  }

  /** Report one WebSocket frame as an `http_request` event with `in: "websocket"` params. */
  websocket(frame: WebSocketFrame): void {
    if (!this.config.enabled) return;
    const params: ParamObservation[] = [];
    const options = { maxDepth: this.config.maxBodyDepth, maxParams: this.config.maxParams };
    if (frame.messageType) params.push(observe("type", "websocket", frame.messageType));

    let payload = frame.message;
    if (typeof payload === "string") {
      // Frames are usually JSON on the wire; flattening them exposes the individual
      // fields a tool fuzzed instead of one opaque blob.
      try {
        const parsed: unknown = JSON.parse(payload);
        if (parsed !== null && typeof parsed === "object") payload = parsed;
      } catch {
        // Not JSON: reported whole, under the name `message`.
      }
    }
    if (payload !== undefined && payload !== null) {
      flattenInto(params, payload, "websocket", "", options);
    }

    this.emit({
      type: "http_request",
      app: this.config.app,
      ts: Date.now() / 1000,
      method: "WEBSOCKET",
      route: frame.route,
      path: frame.path ?? frame.route,
      params,
      ...(frame.authSubject !== undefined ? { auth_subject: frame.authSubject } : {}),
      ...(frame.clientIp ? { client_ip: frame.clientIp } : {}),
      ...(frame.synthetic ? { synthetic: true } : {}),
    } satisfies HttpRequestEvent);
  }

  /** Force a flush. For tests and shutdown hooks; never called on a request path. */
  async flush(): Promise<void> {
    await this.transport.flush();
  }

  stats(): TransportStats {
    return this.transport.getStats();
  }

  async shutdown(): Promise<void> {
    await this.transport.shutdown();
  }
}

/**
 * Symbol used to hang per-request state off an Express request.
 *
 * `Symbol.for` rather than a module-local symbol so that a target which somehow ends
 * up with two copies of the SDK still shares one state slot per request.
 */
export const BENCH_REQUEST_STATE = Symbol.for("ptaas-bench.request-state");

export interface RequestState {
  extraParams: ParamObservation[];
}

function attachParams(req: unknown, params: ParamObservation[]): boolean {
  const state = (req as Record<symbol, RequestState | undefined>)?.[BENCH_REQUEST_STATE];
  if (!state) return false;
  state.extraParams.push(...params);
  return true;
}
