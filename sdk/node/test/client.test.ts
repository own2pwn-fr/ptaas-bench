import express from "express";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { Bench } from "../src/client.js";
import { benchMiddleware } from "../src/middleware.js";
import { resolveConfig } from "../src/config.js";
import type { HttpRequestEvent, NoteEvent, TriggerEvent } from "../src/types.js";
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

async function drain<T>(pick: (events: unknown[]) => T[], min = 1): Promise<T[]> {
  await bench.flush();
  await collector.waitFor(() => pick(collector.events).length >= min);
  return pick(collector.events);
}

describe("configuration", () => {
  it("reads BENCH_APP and BENCH_COLLECTOR_URL from the environment", () => {
    const config = resolveConfig({}, {
      BENCH_APP: "shopfront",
      BENCH_COLLECTOR_URL: "http://collector:8900/",
    } as NodeJS.ProcessEnv);
    expect(config.app).toBe("shopfront");
    // Trailing slash stripped so the POST path is never doubled.
    expect(config.collectorUrl).toBe("http://collector:8900");
    expect(config.enabled).toBe(true);
  });

  it("stays disabled outside the benchmark, so a target behaves identically", () => {
    expect(resolveConfig({}, {} as NodeJS.ProcessEnv).enabled).toBe(false);
    expect(resolveConfig({ app: "x" }, { BENCH_ENABLED: "0" } as NodeJS.ProcessEnv).enabled).toBe(false);
  });

  it("clamps the batch size to the collector's documented maximum", () => {
    expect(resolveConfig({ app: "x", batchSize: 100_000 }, {} as NodeJS.ProcessEnv).batchSize).toBe(500);
  });
});

describe("bench.trigger", () => {
  it("emits a trigger event with the catalog's oracle kind and the evidence", async () => {
    bench.trigger("BENCH-SHOP-0001", {
      oracleKind: "sink",
      payload: "x' UNION SELECT id,email FROM users--",
      detail: "2 rows from users",
      requestId: "req-1",
    });
    const [event] = await drain((e) => e.filter((x): x is TriggerEvent => (x as TriggerEvent).type === "trigger"));

    expect(event).toMatchObject({
      type: "trigger",
      app: "shopfront",
      vuln_id: "BENCH-SHOP-0001",
      oracle_kind: "sink",
      evidence: {
        payload: "x' UNION SELECT id,email FROM users--",
        detail: "2 rows from users",
        request_id: "req-1",
      },
    });
  });

  it("clamps evidence to the collector's 1024-char limit", async () => {
    bench.trigger("BENCH-SHOP-0031", { payload: "A".repeat(5000) });
    const [event] = await drain((e) => e.filter((x): x is TriggerEvent => (x as TriggerEvent).type === "trigger"));
    expect(event!.evidence?.payload).toHaveLength(1024);
  });

  it("degrades a malformed vuln id to a note rather than poisoning the batch", async () => {
    bench.trigger("not-a-vuln-id");
    const notes = await drain((e) => e.filter((x): x is NoteEvent => (x as NoteEvent).type === "note"));
    expect(notes.some((n) => n.message?.includes("malformed vuln id"))).toBe(true);
    expect(collector.events.some((e) => e.type === "trigger")).toBe(false);
  });

  it("never throws, whatever it is handed", () => {
    const circular: Record<string, unknown> = {};
    circular.self = circular;
    expect(() => bench.trigger(undefined as unknown as string)).not.toThrow();
    expect(() => bench.trigger("BENCH-SHOP-0001", { payload: circular as unknown as string })).not.toThrow();
    expect(() => bench.note(undefined as unknown as string)).not.toThrow();
  });
});

describe("graphql helper", () => {
  it("reports the operation name and flattened variables as in:graphql", async () => {
    bench.graphql({
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
    app.use(benchMiddleware({ bench }));
    app.use(express.json());
    app.post("/graphql", (req, res) => {
      const body = req.body as { operationName?: string; variables?: Record<string, unknown> };
      bench.graphql({ operationName: body.operationName, variables: body.variables }, req);
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
    bench.websocket({
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
    bench.websocket({ route: "/ws/orders", message: "PING <script>alert(1)</script>" });
    const [event] = await drain((e) =>
      e.filter((x): x is HttpRequestEvent => (x as HttpRequestEvent).type === "http_request"),
    );
    expect(event!.params?.[0]).toMatchObject({ name: "body", in: "websocket" });
    expect(event!.params?.[0]?.sample).toBe("PING <script>alert(1)</script>");
  });
});

describe("synthetic flagging", () => {
  it("flags requests carrying the self-test header", async () => {
    await fetch(`${target.url}/api/products?q=laptop`, { headers: { "x-bench-selftest": "1" } });
    const [event] = await drain((e) =>
      e.filter((x): x is HttpRequestEvent => (x as HttpRequestEvent).type === "http_request"),
    );
    expect(event!.synthetic).toBe(true);
  });

  it("flags requests from the seeder user-agent", async () => {
    await fetch(`${target.url}/api/products`, { headers: { "user-agent": "ptaas-bench-seeder/1.0" } });
    const [event] = await drain((e) =>
      e.filter((x): x is HttpRequestEvent => (x as HttpRequestEvent).type === "http_request"),
    );
    expect(event!.synthetic).toBe(true);
  });

  it("leaves ordinary tool traffic unflagged", async () => {
    await fetch(`${target.url}/api/products`, { headers: { "user-agent": "ZAP/2.15" } });
    const [event] = await drain((e) =>
      e.filter((x): x is HttpRequestEvent => (x as HttpRequestEvent).type === "http_request"),
    );
    expect(event!.synthetic).toBeUndefined();
    expect(event!.user_agent).toBe("ZAP/2.15");
  });

  it("honours a custom self-test header and seeder user-agent", () => {
    const config = resolveConfig({ app: "x", selftestHeader: "X-Probe", seederUserAgent: "acme-crawler" }, {});
    expect(config.selftestHeader).toBe("x-probe");
    expect(config.seederUserAgent?.test("Acme-Crawler/2")).toBe(true);
    expect(config.seederUserAgent?.test("ZAP/2.15")).toBe(false);
  });
});

describe("auth subject", () => {
  it("reports the authenticated principal for differential oracles", async () => {
    const app = express();
    app.use((req, _res, next) => {
      (req as { user?: { id: string } }).user = { id: "customer-1" };
      next();
    });
    app.use(benchMiddleware({ bench }));
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
