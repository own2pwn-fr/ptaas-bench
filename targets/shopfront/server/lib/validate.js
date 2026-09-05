/**
 * Input validation.
 *
 * Hand-rolled and small on purpose: the API has one convention (reject with 422 and a
 * `details` map of field -> message) and every handler in the service uses it, so a
 * client can render a form error without knowing which endpoint it called.
 */
import { unprocessable } from "./errors.js";

export class Fields {
  constructor(source, location) {
    this.source = source ?? {};
    this.location = location;
    this.problems = {};
    this.values = {};
  }

  #fail(name, message) {
    if (!(name in this.problems)) this.problems[name] = message;
    return undefined;
  }

  string(name, { required = false, min = 0, max = 4096, pattern = null, fallback = undefined } = {}) {
    const raw = this.source[name];
    if (raw === undefined || raw === null || raw === "") {
      if (required) return this.#fail(name, "This field is required.");
      this.values[name] = fallback;
      return fallback;
    }
    if (typeof raw !== "string") return this.#fail(name, "Expected a string.");
    if (raw.length < min) return this.#fail(name, `Must be at least ${min} characters.`);
    if (raw.length > max) return this.#fail(name, `Must be at most ${max} characters.`);
    if (pattern && !pattern.test(raw)) return this.#fail(name, "That value is not in the expected format.");
    this.values[name] = raw;
    return raw;
  }

  integer(name, { required = false, min = null, max = null, fallback = undefined } = {}) {
    const raw = this.source[name];
    if (raw === undefined || raw === null || raw === "") {
      if (required) return this.#fail(name, "This field is required.");
      this.values[name] = fallback;
      return fallback;
    }
    const n = typeof raw === "number" ? raw : Number.parseInt(String(raw), 10);
    if (!Number.isFinite(n) || !Number.isInteger(n)) return this.#fail(name, "Expected a whole number.");
    if (min !== null && n < min) return this.#fail(name, `Must be ${min} or more.`);
    if (max !== null && n > max) return this.#fail(name, `Must be ${max} or less.`);
    this.values[name] = n;
    return n;
  }

  boolean(name, { fallback = undefined } = {}) {
    const raw = this.source[name];
    if (raw === undefined || raw === null || raw === "") {
      this.values[name] = fallback;
      return fallback;
    }
    if (typeof raw === "boolean") {
      this.values[name] = raw;
      return raw;
    }
    const v = String(raw).toLowerCase();
    if (["1", "true", "yes", "on"].includes(v)) return (this.values[name] = true);
    if (["0", "false", "no", "off"].includes(v)) return (this.values[name] = false);
    return this.#fail(name, "Expected true or false.");
  }

  oneOf(name, allowed, { required = false, fallback = undefined } = {}) {
    const raw = this.source[name];
    if (raw === undefined || raw === null || raw === "") {
      if (required) return this.#fail(name, "This field is required.");
      this.values[name] = fallback;
      return fallback;
    }
    const v = String(raw);
    if (!allowed.includes(v)) return this.#fail(name, `Must be one of: ${allowed.join(", ")}.`);
    this.values[name] = v;
    return v;
  }

  email(name, { required = false } = {}) {
    return this.string(name, { required, max: 254, pattern: /^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$/ });
  }

  /** Raise 422 if anything failed. Called once, at the end of the checks. */
  done() {
    if (Object.keys(this.problems).length > 0) {
      throw unprocessable("Some fields need attention.", this.problems);
    }
    return this.values;
  }
}

export const body = (req) => new Fields(req.body, "body");
export const query = (req) => new Fields(req.query, "query");
export const params = (req) => new Fields(req.params, "path");

/** Shared pagination, so every list endpoint answers to the same two parameters. */
export function paging(req, { defaultLimit = 24, maxLimit = 100 } = {}) {
  const f = query(req);
  const limit = f.integer("limit", { min: 1, max: maxLimit, fallback: defaultLimit });
  const page = f.integer("page", { min: 1, max: 10_000, fallback: 1 });
  f.done();
  return { limit, page, offset: (page - 1) * limit };
}
