/**
 * Wire types for the telemetry backend.
 *
 * Property names are snake_case because they are serialised straight to JSON for the
 * collector; renaming one here would be accepted silently by the ingest endpoint and
 * simply lose the field, so they are treated as a fixed contract.
 */

/** Where an input reached the handler from. */
export type AttributeSource =
  | "query"
  | "body"
  | "json"
  | "path"
  | "header"
  | "cookie"
  | "multipart"
  | "raw"
  | "graphql"
  | "websocket";

/**
 * One observed input, hashed rather than stored raw.
 *
 * Request values routinely contain personal data, credentials and card numbers, so
 * only a digest, a length and a short prefix leave the process. The digest is still
 * enough for the backend to tell an endpoint called with its documented default value
 * from one called with something else, which is what the input-drift dashboards need.
 */
export interface Attribute {
  name: string;
  in: AttributeSource;
  value_sha256?: string;
  value_len?: number;
  /** Truncated raw value (<= 256 chars), kept so a human can read a dashboard row. */
  sample?: string;
}

export interface EventBase {
  type: string;
  app: string;
  ts?: number;
  /**
   * Traffic from the platform's own synthetic monitoring probes rather than from a
   * real client. Recorded, but kept out of the service-level statistics: uptime probes
   * would otherwise dominate the request mix of a quiet endpoint.
   */
  synthetic?: boolean;
}

export interface HttpRequestEvent extends EventBase {
  type: "http_request";
  method: string;
  /** Framework route template (`/api/orders/:id`), never a concrete URL. */
  route: string;
  path?: string;
  status?: number;
  auth_subject?: string | null;
  client_ip?: string;
  user_agent?: string;
  params?: Attribute[];
}

/** An application-level counter: something the service itself decided was anomalous. */
export interface SignalEvent extends EventBase {
  type: "signal";
  /** Dotted metric name, e.g. `shop.catalog.query.plan_anomaly`. */
  signal: string;
  attributes?: {
    payload?: string;
    detail?: string;
    request_id?: string;
  };
}

export interface NoteEvent extends EventBase {
  type: "note";
  message?: string;
}

export interface OobEvent extends EventBase {
  type: "oob";
  token: string;
  channel: "dns" | "http" | "https" | "smtp" | "ldap";
  source_ip?: string;
  raw?: string;
}

export type TelemetryEvent = HttpRequestEvent | SignalEvent | NoteEvent | OobEvent;

/**
 * Declares an outbound request the service is about to make on behalf of a caller, so
 * the egress resolver's log can be attributed back to the request that caused it.
 */
export interface EgressCorrelation {
  app: string;
  ts?: number;
  signal: string;
  destination_host: string;
  route?: string;
  param?: string;
  request_id: string;
  synthetic?: boolean;
}

/** Route template reported when no framework route matched the request. */
export const UNMATCHED_ROUTE = "<unmatched>";

/**
 * Metric naming convention: lowercase dotted segments, at least two of them.
 *
 * Enforced locally because the ingest endpoint rejects a whole batch when one event
 * fails validation, which would discard unrelated events queued alongside it.
 */
export const SIGNAL_NAME_PATTERN = /^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$/;
