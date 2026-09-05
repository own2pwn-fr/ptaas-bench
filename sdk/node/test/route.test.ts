import { afterAll, beforeAll, beforeEach, describe, expect, it } from "vitest";

import { TelemetryClient } from "../src/client.js";
import { UNMATCHED_ROUTE } from "../src/types.js";
import { buildApp, listen, type TestApp } from "./app-fixture.js";
import { FakeCollector } from "./fake-collector.js";

const collector = new FakeCollector();
let client: TelemetryClient;
let target: TestApp;

beforeAll(async () => {
  const endpoint = await collector.start();
  client = new TelemetryClient({ service: "shopfront", endpoint, flushIntervalMs: 10 }, {});
  target = await listen(buildApp(client));
});

afterAll(async () => {
  await client.shutdown();
  await target.close();
  await collector.stop();
});

beforeEach(() => collector.reset());

/** Drive one request and return the single http_request event it produced. */
async function trace(path: string, init?: RequestInit) {
  await fetch(`${target.url}${path}`, init);
  await client.flush();
  await collector.waitFor(() => collector.httpEvents().length > 0);
  const events = collector.httpEvents();
  expect(events).toHaveLength(1);
  return events[0]!;
}

describe("route template extraction", () => {
  it("reports the template of a top-level route", async () => {
    const event = await trace("/health");
    expect(event.route).toBe("/health");
    expect(event.path).toBe("/health");
    expect(event.method).toBe("GET");
    expect(event.status).toBe(200);
  });

  it("reports the root route as /", async () => {
    expect((await trace("/")).route).toBe("/");
  });

  it("composes the mount prefix with the router-local path", async () => {
    const event = await trace("/api/products?q=laptop");
    expect(event.route).toBe("/api/products");
    // The concrete URL is reported separately and must not carry the query string.
    expect(event.path).toBe("/api/products");
  });

  it("composes prefixes across nested routers and keeps the param placeholder", async () => {
    const event = await trace("/api/orders/1002");
    expect(event.route).toBe("/api/orders/:id");
    expect(event.path).toBe("/api/orders/1002");
  });

  it("handles several params across a doubly nested mount", async () => {
    const event = await trace("/api/orders/1002/items/SKU-9", { method: "POST" });
    expect(event.route).toBe("/api/orders/:id/items/:sku");
    expect(event.path).toBe("/api/orders/1002/items/SKU-9");
  });

  it("reports <unmatched> with the real path when nothing matched", async () => {
    const event = await trace("/does/not/exist?probe=1");
    expect(event.route).toBe(UNMATCHED_ROUTE);
    expect(event.path).toBe("/does/not/exist");
    expect(event.status).toBe(404);
  });

  it("reports <unmatched> for a known path with the wrong method", async () => {
    const event = await trace("/health", { method: "DELETE" });
    expect(event.route).toBe(UNMATCHED_ROUTE);
  });

  it("keeps every alternative of an array route", async () => {
    const event = await trace("/api/alias-b");
    expect(event.route).toBe("/api/alias-a|/alias-b");
  });

  it("does not leak the route template into the response", async () => {
    const response = await fetch(`${target.url}/api/orders/7`);
    const headerNames = [...response.headers.keys()].map((h) => h.toLowerCase());
    expect(headerNames.some((h) => h.includes("client"))).toBe(false);
    expect(await response.text()).toBe(JSON.stringify({ id: "7" }));
  });
});
