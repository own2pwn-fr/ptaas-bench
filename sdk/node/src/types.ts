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
   * Address of the peer that opened the connection, straight from the socket.
   *
   * Distinct from `client_ip` on purpose. `client_ip` honours `trust proxy` and so is
   * ultimately whatever `X-Forwarded-For` said, which any caller can write; it is kept
   * because it is usually the address a human wants to see, but it is description, not
   * evidence. Anything that decides how an event is treated reads `peer_ip`.
   */
  peer_ip?: string;
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
  /**
   * Virtual host the request was addressed to: the `Host` header (or `:authority` over
   * HTTP/2), lowercased with any port removed.
   *
   * One process commonly serves several names, and the same path can be configured
   * differently on each. Without this, every name sharing a path is indistinguishable
   * in the data, and anything aggregated per route silently merges them.
   *
   * Omitted entirely when the request carried no host. An absent value is a fact worth
   * knowing; a default would be a wrong value that nothing downstream could question.
   */
  host?: string;
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
  /** Socket peer of the request that caused the outbound call. Never a header value. */
  peer_ip?: string;
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
 * Metric naming convention: lowercase dotted segments, at least three of them.
 *
 * Copied verbatim from the metric registry's schema, which is the authority — the
 * ingest endpoint applies the same rule. A name this client accepted but the registry
 * did not would be taken by one endpoint and rejected by another: the counter would
 * appear on dashboards while the egress correlation carrying the same name was dropped
 * on the floor, with nothing logged at either end. Validating locally, at the call
 * site, is the only place a developer ever sees the mistake.
 */
export const SIGNAL_NAME_PATTERN = /^[a-z][a-z0-9]*(\.[a-z0-9_]+){2,}$/;
