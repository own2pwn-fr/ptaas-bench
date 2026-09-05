import express from "express";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { TelemetryClient } from "../src/client.js";
import { telemetryMiddleware } from "../src/middleware.js";
import { resolveConfig } from "../src/config.js";
import type { HttpRequestEvent, NoteEvent, SignalEvent } from "../src/types.js";
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

async function drain<T>(pick: (events: unknown[]) => T[], min = 1): Promise<T[]> {
  await client.flush();
  await collector.waitFor(() => pick(collector.events).length >= min);
  return pick(collector.events);
}

describe("configuration", () => {
  it("reads the service name and endpoint from the environment", () => {
    const config = resolveConfig({}, {
      TELEMETRY_SERVICE: "shopfront",
      TELEMETRY_ENDPOINT: "http://otel-collector:8900/",
    } as NodeJS.ProcessEnv);
    expect(config.service).toBe("shopfront");
    // Trailing slash stripped so the POST path is never doubled.
    expect(config.endpoint).toBe("http://otel-collector:8900");
    expect(config.enabled).toBe(true);
  });

  it("posts to an OTLP-shaped path by default, overridable without a code change", () => {
    expect(resolveConfig({ service: "x" }, {} as NodeJS.ProcessEnv).eventsPath).toBe("/v1/traces");
    const overridden = resolveConfig({}, {
      TELEMETRY_SERVICE: "x",
      TELEMETRY_EVENTS_PATH: "v1/events",
    } as NodeJS.ProcessEnv);
    expect(overridden.eventsPath).toBe("/v1/events");
  });

  it("stays inert unless configured, so an unconfigured process behaves identically", () => {
    expect(resolveConfig({}, {} as NodeJS.ProcessEnv).enabled).toBe(false);
    expect(
      resolveConfig({ service: "x" }, { TELEMETRY_ENABLED: "0" } as NodeJS.ProcessEnv).enabled,
    ).toBe(false);
  });

  it("clamps the batch size to the documented ingest maximum", () => {
    expect(
      resolveConfig({ service: "x", batchSize: 100_000 }, {} as NodeJS.ProcessEnv).batchSize,
    ).toBe(500);
  });
});

describe("client.signal", () => {
  it("records a metric-shaped signal with its attributes", async () => {
    client.signal("shop.catalog.query.plan_anomaly", {
      payload: "x' UNION SELECT id,email FROM users--",
      detail: "2 rows from users",
      requestId: "req-1",
    });
    const [event] = await drain((e) =>
      e.filter((x): x is SignalEvent => (x as SignalEvent).type === "signal"),
    );

    expect(event).toMatchObject({
      type: "signal",
      app: "shopfront",
      signal: "shop.catalog.query.plan_anomaly",
      attributes: {
        payload: "x' UNION SELECT id,email FROM users--",
        detail: "2 rows from users",
        request_id: "req-1",
      },
    });
    // No catalog identifier leaves the process: the mapping lives in the platform.
    expect(JSON.stringify(event)).not.toMatch(/BENCH-/);
  });

  it("clamps free text to the backend's 1024-char limit", async () => {
    client.signal("shop.imports.fetch.egress", { payload: "A".repeat(5000) });
    const [event] = await drain((e) =>
      e.filter((x): x is SignalEvent => (x as SignalEvent).type === "signal"),
    );
    expect(event!.attributes?.payload).toHaveLength(1024);
  });

  it("requires at least three segments, matching the metric registry", async () => {
    // Two segments used to pass here and be rejected downstream. The counter would
    // still have shown up, while the egress correlation using the same name was
    // dropped in silence -- so a blind flaw in that code path would look like a code
    // path nothing ever reached. Rejecting at the call site is the whole point.
    client.signal("shop.catalog");
    const notes = await drain(
      (e) => e.filter((x): x is NoteEvent => (x as NoteEvent).type === "note"),
      1,
    );
    expect(notes[0]!.message).toContain('invalid name "shop.catalog"');
    expect(collector.events.some((e) => e.type === "signal")).toBe(false);
  });

  it("accepts registry-shaped names and rejects everything else", async () => {
    const bad = [
      "shop.catalog", // two segments
      "shop", // one segment
      "plan_anomaly.query.shop", // underscore in the leading segment
      "Shop.Catalog.Query", // uppercase
      "shop..query", // empty segment
      "shop.catalog.query ", // trailing space
      "1shop.catalog.query", // leading digit
      "",
    ];
    for (const name of bad) client.signal(name);
    client.signal("shop.catalog.query.plan_anomaly");
    client.signal("shop.catalog.query");

    const notes = await drain(
      (e) => e.filter((x): x is NoteEvent => (x as NoteEvent).type === "note"),
      bad.length,
    );
    expect(notes.filter((n) => n.message?.includes("invalid name"))).toHaveLength(bad.length);
    // The valid ones still made it: one bad name must not poison the batch.
    expect(collector.events.filter((e) => e.type === "signal")).toHaveLength(2);
  });

  it("never throws, whatever it is handed", () => {
    const circular: Record<string, unknown> = {};
    circular.self = circular;
    expect(() => client.signal(undefined as unknown as string)).not.toThrow();
    expect(() =>
      client.signal("shop.catalog.query.plan_anomaly", { payload: circular as unknown as string }),
    ).not.toThrow();
    expect(() => client.note(undefined as unknown as string)).not.toThrow();
  });
});

describe("client.correlate", () => {
  it("declares an outbound destination immediately, without waiting for a flush", async () => {
    const queuedBefore = client.stats().queued;
    const requestId = client.correlate({
      signal: "shop.imports.fetch.egress",
      destinationHost: "a1b2c3.oob.example",
      route: "/api/imports",
      param: "source_url",
    });

    // Not queued with the events: the DNS lookup it describes happens microseconds
    // later, so the declaration goes out on its own rather than waiting for a tick.
    expect(client.stats().queued).toBe(queuedBefore);
    await collector.waitFor(() => collector.correlations.length === 1);

    expect(collector.correlations[0]).toMatchObject({
      app: "shopfront",
      signal: "shop.imports.fetch.egress",
      destination_host: "a1b2c3.oob.example",
      route: "/api/imports",
      param: "source_url",
      request_id: requestId,
    });
    expect(requestId).toMatch(/[0-9a-f-]{8,}/);
  });

  it("honours a caller-supplied correlation id and never throws", async () => {
    expect(
      client.correlate({ signal: "shop.imports.fetch.egress", destinationHost: "x.example", requestId: "rid-9" }),
    ).toBe("rid-9");
    expect(() =>
      client.correlate({
        signal: "shop.imports.fetch.egress",
        destinationHost: undefined as unknown as string,
      }),
    ).not.toThrow();
    await collector.waitFor(() => collector.correlations.length >= 2);
  });

  it("applies the same naming rule, since the endpoint drops unknown names silently", async () => {
    const requestId = client.correlate({
      signal: "shop.imports",
      destinationHost: "a1b2c3.oob.example",
    });

    // The caller's code path is unchanged -- it still gets an id to thread through --
    // but the problem is now visible instead of costing a whole vulnerability class.
    expect(requestId).toMatch(/[0-9a-f-]{8,}/);
    const notes = await drain(
      (e) => e.filter((x): x is NoteEvent => (x as NoteEvent).type === "note"),
      1,
    );
    expect(notes[0]!.message).toContain('rejected correlation with invalid name "shop.imports"');
    expect(collector.correlations).toHaveLength(0);
  });
});

describe("graphql helper", () => {
  it("reports the operation name and flattened variables as in:graphql", async () => {
    client.graphql({
      operationName: "OrderById",
      variables: { id: "1002", filter: { status: "paid" } },
    });
    const [event] = await drain((e) =>
      e.filter((x): x is HttpRequestEvent => (x as HttpRequestEvent).type === "http_request"),
    );

    expect(event!.route).toBe("/graphql");
    const names = (event!.params ?? []).map((p) => `${p.in}:${p.name}`);
    expect(names).toContain("graphql:operationName");
    expect(names).toContain("graphql:variables.id");
    expect(names).toContain("graphql:variables.filter.status");
  });

  it("folds into the in-flight http_request event when given the request", async () => {
    const app = express();
    app.use(telemetryMiddleware({ client }));
    app.use(express.json());
    app.post("/graphql", (req, res) => {
      const body = req.body as { operationName?: string; variables?: Record<string, unknown> };
      client.graphql({ operationName: body.operationName, variables: body.variables }, req);
      res.json({ data: null });
    });
    const gql = await listen(app);

    await fetch(`${gql.url}/graphql`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ operationName: "OrderById", variables: { id: "1002" } }),
    });
    const events = await drain((e) =>
      e.filter((x): x is HttpRequestEvent => (x as HttpRequestEvent).type === "http_request"),
    );
    await gql.close();

    // One event, not two: the HTTP layer and the GraphQL layer describe the same
    // request, and a duplicate would double-count reach.
    expect(events).toHaveLength(1);
    const names = (events[0]!.params ?? []).map((p) => `${p.in}:${p.name}`);
    expect(events[0]!.route).toBe("/graphql");
    expect(names).toContain("json:operationName");
    expect(names).toContain("graphql:operationName");
    expect(names).toContain("graphql:variables.id");
  });
});

describe("websocket helper", () => {
  it("flattens a JSON frame into in:websocket params", async () => {
    client.websocket({
      route: "/ws/orders",
      messageType: "subscribe",
      message: JSON.stringify({ channel: "orders", filter: { id: "1002" } }),
      authSubject: "customer-1",
      clientIp: "10.0.0.9",
    });
    const [event] = await drain((e) =>
      e.filter((x): x is HttpRequestEvent => (x as HttpRequestEvent).type === "http_request"),
    );

    expect(event).toMatchObject({ method: "WEBSOCKET", route: "/ws/orders", auth_subject: "customer-1" });
    const names = (event!.params ?? []).map((p) => `${p.in}:${p.name}`);
    expect(names).toContain("websocket:type");
    expect(names).toContain("websocket:channel");
    expect(names).toContain("websocket:filter.id");
  });

  it("reports a non-JSON frame whole", async () => {
    client.websocket({ route: "/ws/orders", message: "PING <script>alert(1)</script>" });
    const [event] = await drain((e) =>
      e.filter((x): x is HttpRequestEvent => (x as HttpRequestEvent).type === "http_request"),
    );
    expect(event!.params?.[0]).toMatchObject({ name: "body", in: "websocket" });
    expect(event!.params?.[0]?.sample).toBe("PING <script>alert(1)</script>");
  });
});

describe("synthetic sources", () => {
  it("marks traffic from a configured source range, with no header involved", async () => {
    // 127.0.0.0/8 stands in for the platform's probe network here.
    const probeClient = new TelemetryClient(
      { service: "shopfront", endpoint: collector.url, flushIntervalMs: 10, syntheticCidrs: ["127.0.0.0/8"] },
      {},
    );
    const probed = await listen(buildApp(probeClient));
    await fetch(`${probed.url}/api/products?q=laptop`);
    await probeClient.flush();
    await collector.waitFor(() => collector.httpEvents().length > 0);
    const event = collector.httpEvents()[0]!;
    await probed.close();
    await probeClient.shutdown();

    expect(event.synthetic).toBe(true);
    // Nothing in the request distinguished it: no header was sent or recorded.
    expect(event.params?.some((p) => p.in === "header" && /selftest|probe/i.test(p.name))).toBe(false);
  });

  it("leaves ordinary traffic unmarked when no range is configured", async () => {
    await fetch(`${target.url}/api/products`, { headers: { "user-agent": "curl/8.5" } });
    const [event] = await drain((e) =>
      e.filter((x): x is HttpRequestEvent => (x as HttpRequestEvent).type === "http_request"),
    );
    expect(event!.synthetic).toBeUndefined();
    expect(event!.user_agent).toBe("curl/8.5");
  });

  it("cannot be claimed by a client through a forwarded-for header", async () => {
    // The decision is made on the socket address. If it honoured `trust proxy`, a
    // caller could mark its own traffic and erase itself from the statistics.
    const spoofClient = new TelemetryClient(
      { service: "shopfront", endpoint: collector.url, flushIntervalMs: 10, syntheticCidrs: ["10.1.2.0/24"] },
      {},
    );
    const app = express();
    app.set("trust proxy", true);
    app.use(telemetryMiddleware({ client: spoofClient }));
    app.get("/api/products", (_req, res) => {
      res.json({ ok: true });
    });
    const spoofed = await listen(app);

    await fetch(`${spoofed.url}/api/products`, { headers: { "x-forwarded-for": "10.1.2.3" } });
    await spoofClient.flush();
    await collector.waitFor(() => collector.httpEvents().length > 0);
    const event = collector.httpEvents()[0]!;
    await spoofed.close();
    await spoofClient.shutdown();

    expect(event.synthetic).toBeUndefined();
    // The forwarded address is still reported, it just does not decide anything.
    expect(event.client_ip).toBe("10.1.2.3");
  });

  it("parses a mixed IPv4/IPv6 range list from the environment", () => {
    const config = resolveConfig({}, {
      TELEMETRY_SERVICE: "x",
      TELEMETRY_SYNTHETIC_CIDRS: "10.9.0.0/16, 192.168.5.7, fd00::/8, not-an-address, 10.0.0.0/99",
    } as NodeJS.ProcessEnv);

    expect(config.syntheticSources.size).toBe(3);
    expect(config.syntheticSources.matches("10.9.4.5")).toBe(true);
    expect(config.syntheticSources.matches("192.168.5.7")).toBe(true);
    expect(config.syntheticSources.matches("192.168.5.8")).toBe(false);
    expect(config.syntheticSources.matches("fd00::1")).toBe(true);
    // A dual-stack socket reports IPv4 peers in mapped form.
    expect(config.syntheticSources.matches("::ffff:10.9.4.5")).toBe(true);
    expect(config.syntheticSources.matches(undefined)).toBe(false);
  });
});

describe("auth subject", () => {
  it("reports the authenticated principal for differential oracles", async () => {
    const app = express();
    app.use((req, _res, next) => {
      (req as { user?: { id: string } }).user = { id: "customer-1" };
      next();
    });
    app.use(telemetryMiddleware({ client }));
    app.get("/api/orders/:id", (_req, res) => res.json({ ok: true }));
    const authed = await listen(app);

    await fetch(`${authed.url}/api/orders/1002`);
    const [event] = await drain((e) =>
      e.filter((x): x is HttpRequestEvent => (x as HttpRequestEvent).type === "http_request"),
    );
    await authed.close();
    expect(event!.auth_subject).toBe("customer-1");
  });

  it("reports null when the caller is anonymous", async () => {
    await fetch(`${target.url}/api/products`);
    const [event] = await drain((e) =>
      e.filter((x): x is HttpRequestEvent => (x as HttpRequestEvent).type === "http_request"),
    );
    expect(event!.auth_subject).toBeNull();
  });
});
