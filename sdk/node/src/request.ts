import { flattenInto, observe, observeRecord, type FlattenOptions } from "./attributes.js";
import type { ResolvedConfig } from "./config.js";
import type { Attribute } from "./types.js";

/**
 * Headers worth recording.
 *
 * Intentionally narrow. Recording every header would bury the few that actually explain
 * a request's behaviour — which virtual host it hit, which proxy forwarded it, where it
 * was linked from — under Accept-* and Sec-* noise, and would inflate every event with
 * values no dashboard ever reads.
 */
const HEADER_ALLOWLIST = new Set([
  "host",
  "referer",
  "referrer",
  "user-agent",
  "origin",
  "content-type",
]);

export interface RequestLike {
  method?: string;
  url?: string;
  originalUrl?: string;
  headers?: Record<string, string | string[] | undefined>;
  query?: unknown;
  params?: Record<string, unknown>;
  body?: unknown;
  files?: unknown;
  file?: unknown;
  cookies?: unknown;
  ip?: string;
  socket?: { remoteAddress?: string };
}

export function headerValue(req: RequestLike, name: string): string | undefined {
  const raw = req.headers?.[name];
  if (raw === undefined) return undefined;
  return Array.isArray(raw) ? raw.join(", ") : raw;
}

/**
 * Parse the `Cookie` header directly rather than trusting `req.cookies`.
 *
 * cookie-parser is not always installed, and a cookie the application never reads is
 * still part of what the client sent — often the most useful part when a session
 * behaves differently between two deployments.
 */
export function parseCookieHeader(header: string | undefined): Record<string, string> {
  const out: Record<string, string> = {};
  if (!header) return out;
  for (const part of header.split(";")) {
    const eq = part.indexOf("=");
    if (eq < 0) continue;
    const name = part.slice(0, eq).trim();
    if (!name) continue;
    let value = part.slice(eq + 1).trim();
    if (value.length >= 2 && value.startsWith('"') && value.endsWith('"')) value = value.slice(1, -1);
    try {
      value = decodeURIComponent(value);
    } catch {
      // Broken percent-escapes are exactly the case worth seeing on a dashboard;
      // keep the raw bytes instead of dropping the observation.
    }
    out[name] = value;
  }
  return out;
}

function bodySource(contentType: string | undefined): "json" | "body" | "multipart" | "raw" {
  if (!contentType) return "body";
  const ct = contentType.toLowerCase();
  if (ct.includes("json")) return "json";
  if (ct.includes("multipart/")) return "multipart";
  if (ct.includes("urlencoded")) return "body";
  return "raw";
}

function collectFiles(out: Attribute[], req: RequestLike, options: FlattenOptions): void {
  const candidates: unknown[] = [];
  if (req.file) candidates.push(req.file);
  if (Array.isArray(req.files)) candidates.push(...req.files);
  else if (req.files && typeof req.files === "object") {
    for (const group of Object.values(req.files as Record<string, unknown>)) {
      if (Array.isArray(group)) candidates.push(...group);
      else if (group) candidates.push(group);
    }
  }

  for (const file of candidates) {
    if (out.length >= options.maxAttributes) return;
    if (!file || typeof file !== "object") continue;
    const f = file as { fieldname?: unknown; originalname?: unknown; name?: unknown };
    const field =
      typeof f.fieldname === "string" ? f.fieldname : typeof f.name === "string" ? f.name : "file";
    // The client-supplied name is the interesting half of a file part; the bytes are
    // not hashed because they can be arbitrarily large and nothing addresses them.
    const filename = typeof f.originalname === "string" ? f.originalname : "";
    out.push(observe(field, "multipart", filename));
  }
}

/**
 * Record every input the handler could have read.
 *
 * Runs once the response has been flushed, so body parsers have already populated
 * `req.body` and none of this work lands in the endpoint's latency.
 */
export function collectAttributes(
  req: RequestLike,
  pathParams: Record<string, unknown>,
  config: ResolvedConfig,
): Attribute[] {
  const out: Attribute[] = [];
  const options: FlattenOptions = {
    maxDepth: config.maxBodyDepth,
    maxAttributes: config.maxAttributes,
  };

  observeRecord(out, req.query, "query", options);
  observeRecord(out, pathParams, "path", options);

  const contentType = headerValue(req, "content-type");
  const source = bodySource(contentType);
  const body = req.body;
  if (body !== undefined && body !== null) {
    if (Buffer.isBuffer(body) || typeof body === "string") {
      if (body.length > 0) out.push(observe("body", source === "json" ? "json" : "raw", body));
    } else if (typeof body === "object") {
      // An empty parsed body is indistinguishable from no body at all, and would add a
      // meaningless `body` row to every GET.
      if (Object.keys(body as object).length > 0) {
        flattenInto(out, body, source === "raw" ? "body" : source, "", options);
      }
    }
  }
  collectFiles(out, req, options);

  const cookies = parseCookieHeader(headerValue(req, "cookie"));
  observeRecord(out, cookies, "cookie", options);
  if (req.cookies && typeof req.cookies === "object") {
    // cookie-parser may expose decoded or signature-verified values the raw header does
    // not; only add names the header did not already yield.
    for (const [name, value] of Object.entries(req.cookies as Record<string, unknown>)) {
      if (name in cookies) continue;
      if (out.length >= options.maxAttributes) break;
      out.push(observe(name, "cookie", value));
    }
  }

  for (const [name, raw] of Object.entries(req.headers ?? {})) {
    if (out.length >= options.maxAttributes) break;
    const lower = name.toLowerCase();
    // `x-*` covers x-forwarded-*, x-request-id, x-api-key and whatever else a service
    // routes on, without maintaining an endless allowlist.
    if (!HEADER_ALLOWLIST.has(lower) && !lower.startsWith("x-")) continue;
    if (raw === undefined) continue;
    out.push(observe(lower, "header", Array.isArray(raw) ? raw.join(", ") : raw));
  }

  return out;
}

/**
 * Address of the peer that actually opened the connection.
 *
 * Not `req.ip`, on purpose: with `trust proxy` enabled that is derived from
 * `X-Forwarded-For`, which any client can set. Membership of the synthetic-probe
 * ranges must not be something a caller can claim for itself, so the decision is made
 * on the socket address alone.
 */
export function peerAddress(req: RequestLike): string | undefined {
  return req.socket?.remoteAddress ?? undefined;
}

/** True when the peer is one of the platform's synthetic monitoring probes. */
export function isSynthetic(req: RequestLike, config: ResolvedConfig): boolean {
  return config.syntheticSources.matches(peerAddress(req));
}

/** Address reported with the event; honours `trust proxy` because it is descriptive. */
export function clientIp(req: RequestLike): string | undefined {
  return req.ip ?? peerAddress(req);
}
