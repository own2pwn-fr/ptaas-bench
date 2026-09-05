import type { ResolvedConfig } from "./config.js";
import { flattenInto, observe, observeRecord, type FlattenOptions } from "./params.js";
import type { ParamObservation } from "./types.js";

/**
 * Headers a handler could plausibly be injected through.
 *
 * Deliberately narrow: dumping every header would bury the signal the scorer needs
 * (did the tool touch `host`? did it forge `x-forwarded-for`?) under Accept-* noise,
 * and would inflate every event with values no catalog entry ever names.
 */
const HEADER_ALLOWLIST = new Set([
  "host",
  "referer",
  "referrer",
  "user-agent",
  "origin",
  "content-type",
]);

/** Everything the platform stamps on its own traffic; never a tool's input. */
const HEADER_DENY_PREFIX = "x-bench-";

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
 * Parse the `Cookie` header ourselves instead of trusting `req.cookies`.
 *
 * Targets are not required to install cookie-parser, and a tool that plants a payload
 * in a cookie the app never reads still deserves credit for having fuzzed it.
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
      // Malformed percent-escapes are exactly the sort of thing a fuzzer sends;
      // keep the raw bytes rather than dropping the observation.
    }
    out[name] = value;
  }
  return out;
}

function bodyLocation(contentType: string | undefined): "json" | "body" | "multipart" | "raw" {
  if (!contentType) return "body";
  const ct = contentType.toLowerCase();
  if (ct.includes("json")) return "json";
  if (ct.includes("multipart/")) return "multipart";
  if (ct.includes("urlencoded")) return "body";
  return "raw";
}

function collectFiles(out: ParamObservation[], req: RequestLike, options: FlattenOptions): void {
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
    if (out.length >= options.maxParams) return;
    if (!file || typeof file !== "object") continue;
    const f = file as { fieldname?: unknown; originalname?: unknown; name?: unknown };
    const field = typeof f.fieldname === "string" ? f.fieldname : typeof f.name === "string" ? f.name : "file";
    // The filename is the injectable half of a file part (traversal, XSS, polyglot
    // extensions); the content is not hashed because it can be arbitrarily large and
    // no catalog entry addresses it.
    const filename = typeof f.originalname === "string" ? f.originalname : "";
    out.push(observe(field, "multipart", filename));
  }
}

/**
 * Enumerate every input the handler could have observed.
 *
 * Runs after the response has been flushed (see the middleware), so body parsers have
 * already populated `req.body`, and so none of this work sits on the response path.
 */
export function collectParams(
  req: RequestLike,
  pathParams: Record<string, unknown>,
  config: ResolvedConfig,
): ParamObservation[] {
  const out: ParamObservation[] = [];
  const options: FlattenOptions = { maxDepth: config.maxBodyDepth, maxParams: config.maxParams };

  observeRecord(out, req.query, "query", options);
  observeRecord(out, pathParams, "path", options);

  const contentType = headerValue(req, "content-type");
  const location = bodyLocation(contentType);
  const body = req.body;
  if (body !== undefined && body !== null) {
    if (Buffer.isBuffer(body) || typeof body === "string") {
      if (Buffer.isBuffer(body) ? body.length > 0 : body.length > 0) {
        out.push(observe("body", location === "json" ? "json" : "raw", body));
      }
    } else if (typeof body === "object") {
      // An empty parsed body is indistinguishable from "no body at all" and would
      // add a meaningless `body` entry to every GET.
      if (Object.keys(body as object).length > 0) {
        flattenInto(out, body, location === "raw" ? "body" : location, "", options);
      }
    }
  }
  collectFiles(out, req, options);

  const cookies = parseCookieHeader(headerValue(req, "cookie"));
  observeRecord(out, cookies, "cookie", options);
  if (req.cookies && typeof req.cookies === "object") {
    // cookie-parser may have decoded (or signed-verified) values the raw header does
    // not expose; only add names the header did not already yield.
    for (const [name, value] of Object.entries(req.cookies as Record<string, unknown>)) {
      if (name in cookies) continue;
      if (out.length >= options.maxParams) break;
      out.push(observe(name, "cookie", value));
    }
  }

  for (const [name, raw] of Object.entries(req.headers ?? {})) {
    if (out.length >= options.maxParams) break;
    const lower = name.toLowerCase();
    if (lower.startsWith(HEADER_DENY_PREFIX)) continue;
    // `x-*` catches x-forwarded-*, x-original-url, x-api-key and every custom header
    // a target might route on, without maintaining an endless allowlist.
    if (!HEADER_ALLOWLIST.has(lower) && !lower.startsWith("x-")) continue;
    if (raw === undefined) continue;
    out.push(observe(lower, "header", Array.isArray(raw) ? raw.join(", ") : raw));
  }

  return out;
}

/** True when the request is platform traffic rather than the tool under test. */
export function isSynthetic(req: RequestLike, config: ResolvedConfig): boolean {
  if (req.headers?.[config.selftestHeader] !== undefined) return true;
  const ua = headerValue(req, "user-agent");
  return Boolean(ua && config.seederUserAgent?.test(ua));
}

export function clientIp(req: RequestLike): string | undefined {
  return req.ip ?? req.socket?.remoteAddress ?? undefined;
}
