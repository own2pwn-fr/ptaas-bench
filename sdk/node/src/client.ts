import { randomUUID } from "node:crypto";

import { flattenInto, observe, rawValue } from "./attributes.js";
import { resolveConfig, type ResolvedConfig, type TelemetryOptions } from "./config.js";
import { currentContext } from "./context.js";
import { Transport, type TransportStats } from "./transport.js";
import {
  SIGNAL_NAME_PATTERN,
  type Attribute,
  type EgressCorrelation,
  type HttpRequestEvent,
  type SignalEvent,
  type TelemetryEvent,
} from "./types.js";

/** Extra context recorded alongside a signal. */
export interface SignalOptions {
  /** The input that produced the anomaly. */
  payload?: string;
  /** What was actually observed, in a form a human can act on. */
  detail?: string;
  /** Correlation id, when the service tracks one. */
  requestId?: string;
  /** Force the synthetic marker (for probes exercising their own code paths). */
  synthetic?: boolean;
}

/** A GraphQL operation as seen by the resolver layer. */
export interface GraphQLOperation {
  operationName?: string | null;
  query?: string | null;
  variables?: Record<string, unknown> | null;
  /** HTTP route the endpoint is mounted on. Defaults to `/graphql`. */
  route?: string;
  method?: string;
  synthetic?: boolean;
}

/** One WebSocket frame observed by the service. */
export interface WebSocketFrame {
  /** Route template the socket is mounted on, e.g. `/ws/orders`. */
  route: string;
  /** Concrete path of the upgrade request, when known. */
  path?: string;
  /** Frame payload. Objects and JSON strings are flattened into dotted attributes. */
  message?: unknown;
  /** Logical message type, recorded as its own attribute. */
  messageType?: string;
  authSubject?: string | null;
  clientIp?: string;
  /** Socket peer of the upgrade request. Never a value taken from a header. */
  peerIp?: string;
  synthetic?: boolean;
}

/** An outbound request the service is about to make with a caller-supplied address. */
export interface EgressDeclaration {
  /** Dotted metric name identifying the code path making the call. */
  signal: string;
  /** Hostname the service is about to resolve and connect to. */
  destinationHost: string;
  /** Route template of the request that caused it. */
  route?: string;
  /** Name of the input the address came from. */
  param?: string;
  /** Correlation id; generated when omitted, and returned. */
  requestId?: string;
  /** Socket peer, when the caller is not running inside an instrumented request. */
  peerIp?: string;
  synthetic?: boolean;
}

const MAX_TEXT_CHARS = 1024;

/**
 * Coerce and clamp a free-text attribute.
 *
 * Call sites will sometimes pass a row, an error or a buffer. Coercing here keeps the
 * event serialisable: an unserialisable value would only be discovered at flush time,
 * where it would take a batch of unrelated events down with it.
 */
function clampText(value: unknown): string | undefined {
  if (value === undefined || value === null) return undefined;
  const text = typeof value === "string" ? value : rawValue(value);
  return text.length > MAX_TEXT_CHARS ? text.slice(0, MAX_TEXT_CHARS) : text;
}

/**
 * The telemetry handle an application talks to.
 *
 * Every public method is total: it swallows its own errors and returns without
 * complaint. Instrumentation that can fail a request is worse than no instrumentation,
 * so the client is written so that the worst outcome is a missing data point.
 */
export class TelemetryClient {
  readonly config: ResolvedConfig;
  private readonly transport: Transport;

  constructor(options: TelemetryOptions = {}, env: NodeJS.ProcessEnv = process.env) {
    this.config = resolveConfig(options, env);
    this.transport = new Transport(this.config);
  }

  get enabled(): boolean {
    return this.config.enabled;
  }

  get service(): string {
    return this.config.service;
  }

  /** Queue a pre-built event, stamping service and timestamp when omitted. */
  emit(event: TelemetryEvent): void {
    if (!this.config.enabled) return;
    try {
      if (!event.app) event.app = this.config.service;
      if (event.ts === undefined) event.ts = Date.now() / 1000;
      // Events raised inside a handler inherit the request they belong to, so a
      // counter incremented three call frames down still says who provoked it.
      // Explicit values win: the middleware has read the socket directly.
      const context = currentContext();
      if (context) {
        if (event.peer_ip === undefined && context.peerIp !== undefined) {
          event.peer_ip = context.peerIp;
        }
        if (event.synthetic === undefined && context.synthetic) event.synthetic = true;
      }
      this.transport.enqueue(event);
    } catch {
      // Unreachable in practice; kept because "never throws" is a property this class
      // is relied upon for, not an aspiration.
    }
  }

  /**
   * Record an application-level anomaly.
   *
   * Increment this where the anomalous *effect* is confirmed, not where a suspicious
   * input arrives. A counter that also counts inputs which turned out to be inert is
   * dominated by noise and stops being usable as an alert.
   *
   * @example
   * ```ts
   * if (plan.includes("UNION") && rows.some((r) => r.__table !== "products")) {
   *   telemetry.signal("shop.catalog.query.plan_anomaly", { payload: q, detail });
   * }
   * ```
   */
  signal(name: string, options: SignalOptions = {}): void {
    if (!this.config.enabled) return;
    if (typeof name !== "string" || !SIGNAL_NAME_PATTERN.test(name)) {
      // The ingest endpoint applies the same naming rule and would reject the whole
      // batch, taking unrelated events with it. Degrade to a note instead.
      this.note(`telemetry: rejected metric with invalid name ${JSON.stringify(name)}`);
      return;
    }
    const event: SignalEvent = {
      type: "signal",
      app: this.config.service,
      ts: Date.now() / 1000,
      signal: name,
      attributes: {
        payload: clampText(options.payload),
        detail: clampText(options.detail),
        request_id: clampText(options.requestId),
      },
    };
    if (options.synthetic) event.synthetic = true;
    this.emit(event);
  }

  /** Free-form annotation, e.g. a startup step or a state transition. */
  note(message: string, options: { synthetic?: boolean } = {}): void {
    this.emit({
      type: "note",
      app: this.config.service,
      ts: Date.now() / 1000,
      message,
      ...(options.synthetic ? { synthetic: true } : {}),
    });
  }

  /**
   * Declare an outbound request whose destination came from the caller.
   *
   * The network's resolver logs every lookup the service makes, but a log line on its
   * own cannot say which request caused it. Declaring the destination first lets the
   * two be joined. Sent immediately rather than batched, because the lookup follows
   * within microseconds; ordering is still not guaranteed, so the backend joins on
   * whichever arrives second.
   *
   * @returns the correlation id, so the caller can attach it to later events.
   */
  correlate(declaration: EgressDeclaration): string {
    const requestId = declaration.requestId ?? safeUuid();
    if (!this.config.enabled) return requestId;
    if (typeof declaration.signal !== "string" || !SIGNAL_NAME_PATTERN.test(declaration.signal)) {
      // The correlation endpoint drops an unregistered name without a word, so a typo
      // here would otherwise cost every outbound call from this code path, silently.
      this.note(
        `telemetry: rejected correlation with invalid name ${JSON.stringify(declaration.signal)}`,
      );
      return requestId;
    }
    try {
      const correlation: EgressCorrelation = {
        app: this.config.service,
        ts: Date.now() / 1000,
        signal: declaration.signal,
        destination_host: String(declaration.destinationHost ?? ""),
        request_id: requestId,
      };
      if (declaration.route) correlation.route = declaration.route;
      if (declaration.param) correlation.param = declaration.param;
      const context = currentContext();
      const peerIp = declaration.peerIp ?? context?.peerIp;
      if (peerIp) correlation.peer_ip = peerIp;
      if (declaration.synthetic ?? context?.synthetic) correlation.synthetic = true;
      this.transport.dispatchCorrelation(correlation);
    } catch {
      // Same contract as everything else here: a missing data point, never a failure.
    }
    return requestId;
  }

  /**
   * Record a GraphQL operation.
   *
   * A single POST to `/graphql` hides which fields were actually touched, so the
   * operation name and each variable are recorded as attributes. Pass the Express
   * request to fold them into that request's event; without it a standalone event is
   * emitted.
   */
  graphql(operation: GraphQLOperation, req?: unknown): void {
    if (!this.config.enabled) return;
    const attributes: Attribute[] = [];
    if (operation.operationName) {
      attributes.push(observe("operationName", "graphql", operation.operationName));
    }
    if (operation.variables) {
      flattenInto(attributes, operation.variables, "graphql", "variables", {
        maxDepth: this.config.maxBodyDepth,
        maxAttributes: this.config.maxAttributes,
      });
    }

    if (req && attachAttributes(req, attributes)) return;

    this.emit({
      type: "http_request",
      app: this.config.service,
      ts: Date.now() / 1000,
      method: operation.method ?? "POST",
      route: operation.route ?? "/graphql",
      path: operation.route ?? "/graphql",
      params: attributes,
      ...(operation.synthetic ? { synthetic: true } : {}),
    } satisfies HttpRequestEvent);
  }

  /** Record one WebSocket frame as a request event with `in: "websocket"` attributes. */
  websocket(frame: WebSocketFrame): void {
    if (!this.config.enabled) return;
    const attributes: Attribute[] = [];
    const options = { maxDepth: this.config.maxBodyDepth, maxAttributes: this.config.maxAttributes };
    if (frame.messageType) attributes.push(observe("type", "websocket", frame.messageType));

    let payload = frame.message;
    if (typeof payload === "string") {
      // Frames are usually JSON on the wire; flattening exposes the individual fields
      // instead of one opaque blob nothing can group by.
      try {
        const parsed: unknown = JSON.parse(payload);
        if (parsed !== null && typeof parsed === "object") payload = parsed;
      } catch {
        // Not JSON: recorded whole, under the name `body`.
      }
    }
    if (payload !== undefined && payload !== null) {
      flattenInto(attributes, payload, "websocket", "", options);
    }

    this.emit({
      type: "http_request",
      app: this.config.service,
      ts: Date.now() / 1000,
      method: "WEBSOCKET",
      route: frame.route,
      path: frame.path ?? frame.route,
      params: attributes,
      ...(frame.authSubject !== undefined ? { auth_subject: frame.authSubject } : {}),
      ...(frame.clientIp ? { client_ip: frame.clientIp } : {}),
      ...(frame.peerIp ? { peer_ip: frame.peerIp } : {}),
      ...(frame.synthetic ? { synthetic: true } : {}),
    } satisfies HttpRequestEvent);
  }

  /** Force a flush. For shutdown hooks and tests; never called on a request path. */
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

function safeUuid(): string {
  try {
    return randomUUID();
  } catch {
    return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
  }
}

/**
 * Key for the per-request state hung off an Express request.
 *
 * `Symbol.for` rather than a module-local symbol, so a process that ends up with two
 * copies of this package still shares one state slot per request.
 */
export const TELEMETRY_REQUEST_STATE = Symbol.for("internal.telemetry.request-state");

export interface RequestState {
  extraAttributes: Attribute[];
}

function attachAttributes(req: unknown, attributes: Attribute[]): boolean {
  const state = (req as Record<symbol, RequestState | undefined>)?.[TELEMETRY_REQUEST_STATE];
  if (!state) return false;
  state.extraAttributes.push(...attributes);
  return true;
}
