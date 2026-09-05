/**
 * The anomaly checks, tested on their own.
 *
 * Each of these decides whether something the service noticed is worth reporting. They
 * are pure functions on purpose: the decision has to be reviewable without a database,
 * and a check that fires on ordinary traffic is worse than no check at all, so the
 * negative cases below matter more than the positive ones.
 */
import { describe, expect, it } from "vitest";

import { deepMerge, mergeWatched, baseObjectDrift } from "../server/lib/merge.js";
import { escapeMarkup, executableConstruct, filterMarkup } from "../server/lib/markup.js";
import { describeDriverError, resultEscaped, statementWidened } from "../server/lib/planwatch.js";
import { decodeState, encodeState, materialise } from "../server/lib/statecodec.js";

describe("markup checks", () => {
  it("finds constructs that run script", () => {
    expect(executableConstruct("<img src=x onerror=fetch(1)>")).toMatch(/event handler/);
    expect(executableConstruct("<script>1</script>")).toBe("script element");
    expect(executableConstruct('<a href="javascript:1">go</a>')).toBe("javascript: url");
    expect(executableConstruct('<iframe srcdoc="<script>1</script>">')).toBeTruthy();
  });

  it("leaves ordinary review prose alone", () => {
    const prose = [
      "Held up well on a wet weekend. Sizing runs small, order the next one up.",
      "Bought two <b>for the shed</b> and one for my mother.",
      "Cheaper than the 3 > 2 offer at the market, and it arrived on a Monday.",
      "Great value — a real 'buy it for life' piece.",
      'Fits my 14" pan exactly.',
    ];
    for (const line of prose) expect(executableConstruct(line)).toBeNull();
  });

  it("strips the constructs the filter is meant to strip", () => {
    expect(filterMarkup("<script>x</script>hello")).toBe("hello");
    expect(filterMarkup("<b>keep</b>")).toBe("<b>keep</b>");
  });

  it("escapes everything when asked to", () => {
    expect(escapeMarkup('<img src=x onerror="a">')).not.toContain("<");
  });
});

describe("statement checks", () => {
  const template = "SELECT id FROM products p WHERE p.is_active AND p.title ILIKE '%%'";

  it("ignores searches that merely look unusual", () => {
    for (const term of ["union jack", "3\" or 4\" pipe", "o'brien", "select a size", "1=1 mat"]) {
      const statement = `SELECT id FROM products p WHERE p.is_active AND p.title ILIKE '%${term}%'`;
      expect(statementWidened(statement, template)).toBe(false);
    }
  });

  it("notices structure the template does not have", () => {
    const statement =
      "SELECT id FROM products p WHERE p.is_active AND p.title ILIKE '%x' " +
      "UNION SELECT id,email,password_hash,1,1 FROM users--%'";
    expect(statementWidened(statement, template)).toBe(true);
  });

  it("only reports when the result left the table", () => {
    const products = [{ id: 2001 }, { id: 2002 }];
    expect(resultEscaped(products, [2001, 2002])).toBeNull();
    expect(resultEscaped([], [2001])).toBeNull();
    const widened = resultEscaped([{ id: 2001 }, { id: 1002 }], [2001, 2002]);
    expect(widened).not.toBeNull();
    expect(widened.foreign).toEqual(["1002"]);
  });

  it("reports a tautology by row count even when every key is a real one", () => {
    const all = [{ id: 2001 }, { id: 2002 }, { id: 2003 }];
    expect(resultEscaped(all, [2001, 2002, 2003])).toBeNull();
    expect(resultEscaped(all, [2001])).not.toBeNull();
  });

  it("describes a driver error without inventing fields", () => {
    const described = describeDriverError({ message: "boom", code: "42601", position: "58" });
    expect(described).toMatchObject({ message: "boom", code: "42601", position: "58" });
  });
});

describe("merge checks", () => {
  it("merges nested objects", () => {
    expect(deepMerge({ a: { b: 1 } }, { a: { c: 2 } })).toEqual({ a: { b: 1, c: 2 } });
  });

  it("reports nothing for an ordinary merge", () => {
    const outcome = mergeWatched({ source: "guest" }, { device: "web", utm: { campaign: "spring" } });
    expect(outcome.added).toEqual([]);
    expect(outcome.result).toEqual({ source: "guest", device: "web", utm: { campaign: "spring" } });
  });

  it("reports a key that ends up visible on unrelated objects", () => {
    const key = `probe${Date.now().toString(36)}`;
    // Parsed rather than written as a literal: `__proto__` in an object literal sets the
    // literal's prototype, while JSON.parse gives it an own property, which is what a
    // request body actually produces.
    const outcome = mergeWatched({}, JSON.parse(`{"__proto__":{"${key}":true}}`));
    try {
      expect(outcome.added).toEqual([key]);
      expect({}[key]).toBe(true);
      expect(baseObjectDrift()).toContain(key);
    } finally {
      delete Object.prototype[key];
    }
  });

  it("does not report the same key twice for two merges", () => {
    const key = `probe2${Date.now().toString(36)}`;
    try {
      expect(mergeWatched({}, JSON.parse(`{"__proto__":{"${key}":1}}`)).added).toEqual([key]);
      expect(mergeWatched({}, JSON.parse(`{"__proto__":{"${key}":2}}`)).added).toEqual([]);
    } finally {
      delete Object.prototype[key];
    }
  });
});

describe("basket state codec", () => {
  it("round-trips", () => {
    const state = { lines: [{ variant_id: 3101, quantity: 2, price: 1200 }], currency: "EUR" };
    expect(decodeState(encodeState(state))).toEqual(state);
  });

  it("evaluates an ordinary rule and reports nothing", () => {
    const resolved = [];
    const out = materialise({ total: { $expr: "subtotal + 495" } }, { subtotal: 2400 }, resolved);
    expect(out.total).toBe(2895);
    expect(resolved).toEqual([]);
  });

  it("reports a rule that reaches outside the rule scope", () => {
    const resolved = [];
    materialise({ total: { $expr: "process.pid" } }, { subtotal: 0 }, resolved);
    expect(resolved).toContain("process");
  });

  it("does not report arithmetic helpers the scope exposes", () => {
    const resolved = [];
    const out = materialise({ total: { $expr: "round(subtotal / 3)" } }, { subtotal: 10 }, resolved);
    expect(out.total).toBe(3);
    expect(resolved).toEqual([]);
  });
});
