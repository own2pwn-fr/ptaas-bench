import { createHash } from "node:crypto";
import type { Attribute, AttributeSource } from "./types.js";

/** Backend limit on `sample`. */
export const SAMPLE_MAX_CHARS = 256;

/**
 * Render a value the way it arrived.
 *
 * Strings pass through untouched, because the digest of a string value has to match
 * the digest the backend computes for the same string from anywhere else in the stack.
 * Anything else has already been decoded by a body parser, so JSON is the closest
 * faithful rendering of what was on the wire.
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
    // Circular or otherwise unserialisable: the name is worth more than the value.
    return "";
  }
}

/** Truncate for display without leaving a broken surrogate pair behind. */
export function truncateSample(raw: string): string {
  if (raw.length <= SAMPLE_MAX_CHARS) return raw;
  const cut = raw.slice(0, SAMPLE_MAX_CHARS);
  const last = cut.charCodeAt(cut.length - 1);
  // A high surrogate at the boundary would serialise as a lone U+D800..DBFF, which
  // some JSON consumers reject outright.
  return last >= 0xd800 && last <= 0xdbff ? cut.slice(0, -1) : cut;
}

export function sha256(raw: string): string {
  return createHash("sha256").update(raw, "utf8").digest("hex");
}

/** Build one attribute. `value_len` is a byte count, matching the hashed bytes. */
export function observe(name: string, source: AttributeSource, value: unknown): Attribute {
  const raw = rawValue(value);
  return {
    name,
    in: source,
    value_sha256: sha256(raw),
    value_len: Buffer.byteLength(raw, "utf8"),
    sample: truncateSample(raw),
  };
}

function isPlainContainer(value: unknown): value is Record<string, unknown> | unknown[] {
  if (value === null || typeof value !== "object") return false;
  if (Buffer.isBuffer(value)) return false;
  if (value instanceof Date) return false;
  const proto = Object.getPrototypeOf(value);
  return Array.isArray(value) || proto === Object.prototype || proto === null;
}

export interface FlattenOptions {
  maxDepth: number;
  maxAttributes: number;
}

/**
 * Flatten a decoded body into one attribute per leaf, using dotted paths
 * (`shipping.address.city`, `items.0.sku`).
 *
 * Dashboards and alerts address a single field by name, so a nested payload has to be
 * reachable under the same flat name a caller would use to talk about it. Empty
 * containers are recorded as leaves, so "the client sent this key" stays visible even
 * when it sent nothing inside it.
 */
export function flattenInto(
  out: Attribute[],
  value: unknown,
  source: AttributeSource,
  prefix: string,
  options: FlattenOptions,
  depth = 0,
): void {
  if (out.length >= options.maxAttributes) return;

  if (isPlainContainer(value) && depth < options.maxDepth) {
    const entries: [string, unknown][] = Array.isArray(value)
      ? value.map((v, i) => [String(i), v] as [string, unknown])
      : Object.entries(value);

    if (entries.length === 0) {
      out.push(observe(prefix || "body", source, value));
      return;
    }
    for (const [key, child] of entries) {
      if (out.length >= options.maxAttributes) return;
      flattenInto(out, child, source, prefix ? `${prefix}.${key}` : key, options, depth + 1);
    }
    return;
  }

  out.push(observe(prefix || "body", source, value));
}

/** Flatten a record of mostly-scalar values (query, path params, cookies). */
export function observeRecord(
  out: Attribute[],
  record: unknown,
  source: AttributeSource,
  options: FlattenOptions,
): void {
  if (!record || typeof record !== "object") return;
  for (const [key, value] of Object.entries(record as Record<string, unknown>)) {
    if (out.length >= options.maxAttributes) return;
    // Query strings nest too (`?filter[a]=b`), so reuse the flattener rather than
    // stringifying a sub-object into an unqueryable blob.
    flattenInto(out, value, source, key, options);
  }
}
