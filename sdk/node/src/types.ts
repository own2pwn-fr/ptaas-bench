/**
 * Wire types for the ptaas-bench collector.
 *
 * These mirror `platform/collector/openapi.yaml`, which is a frozen contract: the
 * scoring engine reads these fields verbatim. Property names are snake_case on
 * purpose — they are serialised straight to JSON, so renaming them here would
 * silently break scoring rather than fail a build.
 */

/** Where an input reached the handler from. Matches the collector enum exactly. */
export type ParamLocation =
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

/** How a planted sink decided the flaw was really exercised. */
export type OracleKind = "sink" | "oob" | "state" | "differential" | "timing" | "artifact";

/**
 * One observable input, hashed rather than stored raw.
 *
 * The scorer compares {@link ParamObservation.value_sha256} against the catalog's
 * `default_value` to tell "the tool visited this parameter" from "the tool actually
 * fuzzed it", which is why the hash must be taken over the *raw* value and not over
 * a normalised or re-serialised one.
 */
export interface ParamObservation {
  name: string;
  in: ParamLocation;
  value_sha256?: string;
  value_len?: number;
  /** Truncated raw value (<= 256 chars) so a human can audit a score. */
  sample?: string;
}

export interface EventBase {
  type: string;
  app: string;
  ts?: number;
  /**
   * Platform-generated traffic (seeding, self-test, health checks). Stored but never
   * scored — otherwise the platform would credit a tool with its own requests.
   */
  synthetic?: boolean;
}

export interface HttpRequestEvent extends EventBase {
  type: "http_request";
  method: string;
  /** Framework route template (`/api/orders/:id`), or `<unmatched>`. Never a concrete URL. */
  route: string;
  path?: string;
  status?: number;
  auth_subject?: string | null;
  client_ip?: string;
  user_agent?: string;
  params?: ParamObservation[];
}

export interface TriggerEvent extends EventBase {
  type: "trigger";
  vuln_id: string;
  oracle_kind?: OracleKind;
  evidence?: {
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

export type BenchEvent = HttpRequestEvent | TriggerEvent | NoteEvent | OobEvent;

/** Route template reported when no framework route matched the request. */
export const UNMATCHED_ROUTE = "<unmatched>";

/**
 * Vulnerability ids are validated against the same pattern the collector enforces.
 * A malformed id would make the collector reject the *whole* batch, taking unrelated
 * events down with it, so the SDK filters locally instead.
 */
export const VULN_ID_PATTERN = /^BENCH-[A-Z0-9]+-\d{4}$/;
