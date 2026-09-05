import { connect } from "node:net";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";

import { TelemetryClient } from "../src/client.js";
import { normaliseHost } from "../src/request.js";
import { UNMATCHED_ROUTE, type HttpRequestEvent } from "../src/types.js";
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

// Drain between tests. A test that reads a response without waiting for the event it
// produced would otherwise have that event land in the next test's window.
afterEach(async () => {
  await client.flush();
  collector.reset();
});

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

/**
 * Send a request over a raw socket.
 *
 * `fetch` treats Host as a forbidden header and silently keeps its own, so the cases
 * that matter here — a Host that differs from the connected address, and a request with
 * no Host at all — are only reachable by writing the request line directly.
 */
async function rawRequest(requestLines: string[]): Promise<HttpRequestEvent> {
  const port = Number(new URL(target.url).port);
  await new Promise<void>((resolve, reject) => {
    const socket = connect(port, "127.0.0.1", () => {
      socket.write(`${requestLines.join("\r\n")}\r\n\r\n`);
    });
    socket.on("data", () => socket.end());
    socket.on("end", () => resolve());
    socket.on("error", reject);
  });
  await client.flush();
  await collector.waitFor(() => collector.httpEvents().length > 0);
  return collector.httpEvents()[0]!;
}

describe("virtual host", () => {
  it("reports the host the request was addressed to, without its port", async () => {
    const event = await trace("/health");
    // fetch sends `Host: 127.0.0.1:<port>`; the port is not part of the vhost.
    expect(event.host).toBe("127.0.0.1");
  });

  it("lowercases it, so one vhost does not split into several", async () => {
    const event = await rawRequest(["GET /api/products HTTP/1.1", "Host: Shop.Example:8443"]);
    expect(event.host).toBe("shop.example");
    expect(event.route).toBe("/api/products");
  });

  it("still reports Host as an input, with the raw value the caller sent", async () => {
    const event = await rawRequest(["GET /api/products HTTP/1.1", "Host: evil.example:8443"]);
    const attribute = event.params?.find((p) => p.in === "header" && p.name === "host");
    // A target in this corpus acts on the Host header, so the untouched value has to
    // survive as an attribute even though a normalised copy is reported separately.
    expect(attribute?.sample).toBe("evil.example:8443");
    expect(event.host).toBe("evil.example");
  });

  it("omits the field entirely when the request named no host", async () => {
    // HTTP/1.0 does not require a Host header, so this is reachable over the wire.
    const event = await rawRequest(["GET /health HTTP/1.0"]);
    // Reported as unresolved rather than defaulted: the scorer can then say so.
    expect(event.host).toBeUndefined();
    expect(event.route).toBe("/health");
  });
});

describe("host normalisation", () => {
  it("strips the port, the userinfo and nothing else", () => {
    expect(normaliseHost("Example.COM")).toBe("example.com");
    expect(normaliseHost("example.com:8080")).toBe("example.com");
    expect(normaliseHost(" example.com:8080 ")).toBe("example.com");
    expect(normaliseHost("user@example.com:80")).toBe("example.com");
    // A trailing dot is left alone: it is a different name to a resolver, and a target
    // that treats it as equal is doing so on purpose.
    expect(normaliseHost("example.com.")).toBe("example.com.");
  });

  it("keeps an IPv6 literal intact instead of truncating it at the first colon", () => {
    expect(normaliseHost("[::1]:8080")).toBe("[::1]");
    expect(normaliseHost("[2001:DB8::1]")).toBe("[2001:db8::1]");
    expect(normaliseHost("[::1")).toBeUndefined();
  });

  it("reports nothing rather than an empty string", () => {
    expect(normaliseHost(undefined)).toBeUndefined();
    expect(normaliseHost("")).toBeUndefined();
    expect(normaliseHost(":8080")).toBeUndefined();
  });
});
