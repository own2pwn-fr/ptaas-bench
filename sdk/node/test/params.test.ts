import { createHash } from "node:crypto";
import { afterAll, beforeAll, beforeEach, describe, expect, it } from "vitest";

import { Bench } from "../src/client.js";
import { observe, rawValue, sha256, SAMPLE_MAX_CHARS, truncateSample } from "../src/params.js";
import { collectParams, parseCookieHeader } from "../src/request.js";
import type { ParamLocation, ParamObservation } from "../src/types.js";
import { buildApp, listen, type TestApp } from "./app-fixture.js";
import { FakeCollector } from "./fake-collector.js";

const collector = new FakeCollector();
let bench: Bench;
let target: TestApp;

beforeAll(async () => {
  const collectorUrl = await collector.start();
  bench = new Bench({ app: "shopfront", collectorUrl, flushIntervalMs: 10 }, {});
  target = await listen(buildApp(bench));
});

afterAll(async () => {
  await bench.shutdown();
  await target.close();
  await collector.stop();
});

beforeEach(() => collector.reset());

async function traceParams(path: string, init?: RequestInit): Promise<ParamObservation[]> {
  await fetch(`${target.url}${path}`, init);
  await bench.flush();
  await collector.waitFor(() => collector.httpEvents().length > 0);
  return collector.httpEvents()[0]!.params ?? [];
}

function find(params: ParamObservation[], name: string, location: ParamLocation) {
  return params.find((p) => p.name === name && p.in === location);
}

describe("value hashing and truncation", () => {
  it("hashes the raw value so the scorer can compare against the catalog default", () => {
    // BENCH-SHOP-0001 has default_value "laptop": an unfuzzed visit must hash to this.
    const expected = createHash("sha256").update("laptop", "utf8").digest("hex");
    expect(sha256("laptop")).toBe(expected);
    expect(observe("q", "query", "laptop").value_sha256).toBe(expected);
  });

  it("reports value_len in bytes, matching the hashed bytes", () => {
    const observation = observe("q", "query", "café");
    expect(observation.value_len).toBe(5);
    expect(observation.value_sha256).toBe(sha256("café"));
  });

  it("truncates the sample to 256 chars but hashes the whole value", () => {
    const long = "A".repeat(1000);
    const observation = observe("q", "query", long);
    expect(observation.sample).toHaveLength(SAMPLE_MAX_CHARS);
    expect(observation.value_len).toBe(1000);
    expect(observation.value_sha256).toBe(sha256(long));
  });

  it("never truncates in the middle of a surrogate pair", () => {
    const raw = `${"a".repeat(SAMPLE_MAX_CHARS - 1)}😀tail`;
    const sample = truncateSample(raw);
    expect(sample).toHaveLength(SAMPLE_MAX_CHARS - 1);
    expect(JSON.parse(JSON.stringify(sample))).toBe(sample);
  });

  it("renders non-string values the way they reached the handler", () => {
    expect(rawValue(42)).toBe("42");
    expect(rawValue(true)).toBe("true");
    expect(rawValue(null)).toBe("");
    expect(rawValue(["a", "b"])).toBe('["a","b"]');
  });
});

describe("cookie header parsing", () => {
  it("splits pairs and percent-decodes without needing cookie-parser", () => {
    expect(parseCookieHeader("sid=abc; theme=%22dark%22; empty=")).toEqual({
      sid: "abc",
      theme: '"dark"',
      empty: "",
    });
  });

  it("keeps malformed percent-escapes rather than dropping the observation", () => {
    expect(parseCookieHeader("payload=%ff%zz")).toEqual({ payload: "%ff%zz" });
  });
});

describe("parameter enumeration across locations", () => {
  it("enumerates query parameters", async () => {
    const params = await traceParams("/api/products?q=laptop&sort=price");
    expect(find(params, "q", "query")?.value_sha256).toBe(sha256("laptop"));
    expect(find(params, "sort", "query")?.sample).toBe("price");
  });

  it("enumerates path parameters from a nested router", async () => {
    const params = await traceParams("/api/orders/1002/items/SKU-9", { method: "POST" });
    // req.params is restored as the router unwinds, so this is the assertion that
    // catches a naive "read it on finish" implementation.
    expect(find(params, "id", "path")?.sample).toBe("1002");
    expect(find(params, "sku", "path")?.sample).toBe("SKU-9");
  });

  it("flattens a nested JSON body into dotted paths", async () => {
    const params = await traceParams("/api/admin/imports", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        source_url: "http://token.oob.bench.local/x",
        options: { retries: 3, auth: { user: "admin" } },
        tags: ["a", "b"],
        empty: {},
      }),
    });

    expect(find(params, "source_url", "json")?.sample).toBe("http://token.oob.bench.local/x");
    expect(find(params, "options.retries", "json")?.sample).toBe("3");
    expect(find(params, "options.auth.user", "json")?.sample).toBe("admin");
    expect(find(params, "tags.0", "json")?.sample).toBe("a");
    expect(find(params, "tags.1", "json")?.sample).toBe("b");
    // An empty container still proves the key was sent.
    expect(find(params, "empty", "json")?.sample).toBe("{}");
  });

  it("enumerates urlencoded bodies as in:body", async () => {
    const params = await traceParams("/api/admin/imports", {
      method: "POST",
      headers: { "content-type": "application/x-www-form-urlencoded" },
      body: "source_url=http%3A%2F%2Fx&depth=2",
    });
    expect(find(params, "source_url", "body")?.sample).toBe("http://x");
    expect(find(params, "depth", "body")?.sample).toBe("2");
  });

  it("enumerates multipart field names and file field names", async () => {
    const form = new FormData();
    form.set("description", "supplier catalogue");
    form.set("catalogue", new Blob(["id,name\n1,x"], { type: "text/csv" }), "catalogue.csv");
    const params = await traceParams("/api/upload", { method: "POST", body: form });

    expect(find(params, "description", "multipart")?.sample).toBe("supplier catalogue");
    // The filename is the injectable half of a file part, so it is what gets hashed.
    expect(find(params, "catalogue", "multipart")?.sample).toBe("catalogue.csv");
  });

  it("reports a traversal filename verbatim when the parser preserved it", async () => {
    // multer strips the directory part unless `preservePath` is set, so this is a
    // unit-level check that the SDK itself never sanitises what it was handed: a
    // path-traversal payload in a filename has to survive into the event.
    const observations = collectParams(
      {
        headers: { "content-type": "multipart/form-data; boundary=x" },
        files: [{ fieldname: "catalogue", originalname: "../../etc/passwd" }],
      },
      {},
      bench.config,
    );
    expect(find(observations, "catalogue", "multipart")?.sample).toBe("../../etc/passwd");
  });

  it("reports an unparsed body as in:raw", async () => {
    const params = await traceParams("/api/admin/imports", {
      method: "POST",
      headers: { "content-type": "application/octet-stream" },
      body: "<?xml version='1.0'?><!DOCTYPE x [<!ENTITY e SYSTEM 'file:///etc/passwd'>]>",
    });
    const raw = find(params, "body", "raw");
    expect(raw?.sample).toContain("file:///etc/passwd");
  });

  it("enumerates cookies", async () => {
    const params = await traceParams("/api/products", {
      headers: { cookie: "session=deadbeef; role=admin" },
    });
    expect(find(params, "session", "cookie")?.sample).toBe("deadbeef");
    expect(find(params, "role", "cookie")?.sample).toBe("admin");
  });

  it("enumerates injection-prone headers, including every x-* header", async () => {
    const params = await traceParams("/api/products", {
      headers: {
        "x-forwarded-for": "127.0.0.1, 10.0.0.1",
        "x-forwarded-host": "evil.example",
        "x-original-url": "/admin",
        referer: "http://ref.example/",
        origin: "http://origin.example",
        "user-agent": "curl/8",
      },
    });
    expect(find(params, "x-forwarded-for", "header")?.sample).toBe("127.0.0.1, 10.0.0.1");
    expect(find(params, "x-forwarded-host", "header")?.sample).toBe("evil.example");
    expect(find(params, "x-original-url", "header")?.sample).toBe("/admin");
    expect(find(params, "referer", "header")).toBeDefined();
    expect(find(params, "origin", "header")).toBeDefined();
    expect(find(params, "user-agent", "header")).toBeDefined();
    expect(find(params, "host", "header")).toBeDefined();
    // Accept-* and friends are noise no catalog entry ever names.
    expect(params.some((p) => p.in === "header" && p.name.startsWith("accept"))).toBe(false);
  });

  it("never reports the platform's own x-bench-* headers as tool input", async () => {
    const params = await traceParams("/api/products", {
      headers: { "x-bench-selftest": "1" },
    });
    expect(params.some((p) => p.name.startsWith("x-bench-"))).toBe(false);
  });

  it("adds no body param to a plain GET", async () => {
    const params = await traceParams("/health");
    expect(params.some((p) => p.in === "json" || p.in === "body" || p.in === "raw")).toBe(false);
  });

  it("bounds the number of observations on a pathological body", async () => {
    const wide: Record<string, number> = {};
    for (let i = 0; i < 5_000; i += 1) wide[`k${i}`] = i;
    const params = await traceParams("/api/admin/imports", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(wide),
    });
    expect(params.length).toBeLessThanOrEqual(512);
  });
});
