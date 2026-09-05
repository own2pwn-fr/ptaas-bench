import { afterAll, beforeAll, beforeEach, describe, expect, it } from "vitest";

import type { HttpRequestEvent, SignalEvent } from "../src/types.js";
import { listen, type TestApp } from "./app-fixture.js";
import { FakeCollector } from "./fake-collector.js";

/**
 * Smoke test for examples/express-minimal.ts.
 *
 * The example is the template every service copies, so it is worth proving the pattern
 * works end to end: an ordinary search records the request and nothing else, a query
 * that actually widened the plan raises the counter exactly once.
 */
const collector = new FakeCollector();
let target: TestApp;
let client: { flush(): Promise<void>; shutdown(): Promise<void> };

beforeAll(async () => {
  const endpoint = await collector.start();
  // The example calls initTelemetry() at module scope, exactly as a service would, so
  // the environment has to be in place before it is imported.
  process.env.TELEMETRY_SERVICE = "shopfront";
  process.env.TELEMETRY_ENDPOINT = endpoint;

  const sdk = await import("../src/index.js");
  const example = await import("../examples/express-minimal.js");
  client = sdk.getTelemetry();
  target = await listen(example.app);
});

afterAll(async () => {
  await client.shutdown();
  await target.close();
  await collector.stop();
  delete process.env.TELEMETRY_SERVICE;
  delete process.env.TELEMETRY_ENDPOINT;
});

beforeEach(() => collector.reset());

async function drive(url: string, init?: RequestInit) {
  const response = await fetch(`${target.url}${url}`, init);
  const body: unknown = await response.json();
  await client.flush();
  await collector.waitFor(() => collector.events.length > 0);
  return {
    body,
    http: collector.events.filter((e): e is HttpRequestEvent => e.type === "http_request"),
    signals: collector.events.filter((e): e is SignalEvent => e.type === "signal"),
  };
}

describe("examples/express-minimal", () => {
  it("records the request against its route template", async () => {
    const { http, signals } = await drive(`/api/products?q=${encodeURIComponent("laptop")}`);
    expect(http[0]?.route).toBe("/api/products");
    expect(http[0]?.params?.some((p) => p.name === "q" && p.in === "query")).toBe(true);
    // An ordinary search is not an anomaly.
    expect(signals).toHaveLength(0);
  });

  it("does not count a suspicious-looking input that changed nothing", async () => {
    const { signals } = await drive(`/api/products?q=${encodeURIComponent("laptop'")}`);
    expect(signals).toHaveLength(0);
  });

  it("counts a plan that actually reached outside the products table, once", async () => {
    const q = "x' UNION SELECT id,email,password_hash,1,1 FROM users--";
    const { body, signals } = await drive(`/api/products?q=${encodeURIComponent(q)}`);

    // The effect is real: rows from another table genuinely came back.
    expect(JSON.stringify(body)).toContain("ada@example.com");
    expect(signals).toHaveLength(1);
    expect(signals[0]).toMatchObject({
      type: "signal",
      signal: "shop.catalog.query.plan_anomaly",
      app: "shopfront",
    });
    expect(signals[0]?.attributes?.payload).toBe(q);
    expect(signals[0]?.attributes?.detail).toContain("users");
  });

  it("declares the destination before an outbound fetch, and echoes the correlation id", async () => {
    const { body } = await drive("/api/imports", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ source_url: `${collector.url}/catalog.json` }),
    });

    await collector.waitFor(() => collector.correlations.length === 1);
    const correlation = collector.correlations[0]!;
    expect(correlation).toMatchObject({
      signal: "shop.imports.fetch.egress",
      destination_host: "127.0.0.1",
      route: "/api/imports",
      param: "source_url",
    });
    expect((body as { request_id?: string }).request_id).toBe(correlation.request_id);
  });

  it("records an unmatched probe with the real path", async () => {
    const { http } = await drive("/wp-admin/setup-config.php");
    expect(http[0]?.route).toBe("<unmatched>");
    expect(http[0]?.path).toBe("/wp-admin/setup-config.php");
    expect(http[0]?.status).toBe(404);
  });
});
