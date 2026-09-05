import { createHash } from "node:crypto";
import type { ParamLocation, ParamObservation } from "./types.js";

/** Schema cap on `sample`. Keep in sync with the collector's `maxLength: 256`. */
export const SAMPLE_MAX_CHARS = 256;

/**
 * Render a value the way it appeared on the wire.
 *
 * Strings pass through untouched — that is the whole point, since the scorer hashes
 * the catalog's `default_value` (a plain string) and compares. Anything else has
 * already been decoded by a body parser, so JSON is the closest faithful rendering.
 */
export function rawValue(value: unknown): string {
  if (typeof value === "string") return value;
  if (value === null || value === undefined) return "";
  if (typeof value === "number" || typeof value === "boolean" || typeof value === "bigint") {
    return String(value);
  }
  if (Buffer.isBuffer(value)) return value.toString("utf8");
  try {
    return JSON.stringify(value) ?? "";
  } catch {
    // Circular or otherwise unserialisable: the name still matters more than the value.
    return "";
  }
}

/** Truncate for the audit sample without leaving a broken surrogate pair behind. */
export function truncateSample(raw: string): string {
  if (raw.length <= SAMPLE_MAX_CHARS) return raw;
  const cut = raw.slice(0, SAMPLE_MAX_CHARS);
  const last = cut.charCodeAt(cut.length - 1);
  // A high surrogate at the boundary would serialise as a lone U+D800..DBFF and
  // some JSON consumers reject it.
  return last >= 0xd800 && last <= 0xdbff ? cut.slice(0, -1) : cut;
}

export function sha256(raw: string): string {
  return createHash("sha256").update(raw, "utf8").digest("hex");
}

/** Build one observation. `value_len` is a byte count, matching the hashed bytes. */
export function observe(name: string, location: ParamLocation, value: unknown): ParamObservation {
  const raw = rawValue(value);
  return {
    name,
    in: location,
    value_sha256: sha256(raw),
    value_len: Buffer.byteLength(raw, "utf8"),
    sample: truncateSample(raw),
  };
}

function isPlainContainer(value: unknown): value is Record<string, unknown> | unknown[] {
  if (value === null || typeof value !== "object") return false;
  if (Buffer.isBuffer(value)) return false;
  if (value instanceof Date) return false;
  return Array.isArray(value) || Object.getPrototypeOf(value) === Object.prototype || Object.getPrototypeOf(value) === null;
}

export interface FlattenOptions {
  maxDepth: number;
  maxParams: number;
}

/**
 * Flatten a decoded body into one observation per leaf, using dotted paths
 * (`shipping.address.city`, `items.0.sku`).
 *
 * Catalog entries name a single parameter (`param: source_url`), so a nested payload
 * has to be addressable by the same flat name a scanner would fuzz. Empty containers
 * are emitted as leaves so that "the tool sent this key" stays visible even when it
 * sent nothing in it.
 */
export function flattenInto(
  out: ParamObservation[],
  value: unknown,
  location: ParamLocation,
  prefix: string,
  options: FlattenOptions,
  depth = 0,
): void {
  if (out.length >= options.maxParams) return;

  if (isPlainContainer(value) && depth < options.maxDepth) {
    const entries: [string, unknown][] = Array.isArray(value)
      ? value.map((v, i) => [String(i), v] as [string, unknown])
      : Object.entries(value);

    if (entries.length === 0) {
      // `{}` / `[]`: no leaves, but the key itself was observed.
      out.push(observe(prefix || "body", location, value));
      return;
    }
    for (const [key, child] of entries) {
      if (out.length >= options.maxParams) return;
      flattenInto(out, child, location, prefix ? `${prefix}.${key}` : key, options, depth + 1);
    }
    return;
  }

  out.push(observe(prefix || "body", location, value));
}

/** Flatten a record of already-scalar values (query, params, cookies). */
export function observeRecord(
  out: ParamObservation[],
  record: unknown,
  location: ParamLocation,
  options: FlattenOptions,
): void {
  if (!record || typeof record !== "object") return;
  for (const [key, value] of Object.entries(record as Record<string, unknown>)) {
    if (out.length >= options.maxParams) return;
    // Query strings and form bodies can nest too (`?filter[a]=b`, `a.b=c` in some
    // parsers), so reuse the same flattener rather than stringifying a sub-object.
    flattenInto(out, value, location, key, options);
  }
}
