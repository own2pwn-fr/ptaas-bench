import express from "express";
import { afterAll, beforeAll, beforeEach, describe, expect, it } from "vitest";

import { TelemetryClient } from "../src/client.js";
import { telemetryMiddleware } from "../src/middleware.js";
import type { HttpRequestEvent, SignalEvent } from "../src/types.js";
import { buildApp, listen, type TestApp } from "./app-fixture.js";
import { FakeCollector } from "./fake-collector.js";

/**
 * `peer_ip` is the address every downstream decision is allowed to rest on.
 *
 * The backend cannot see the original caller — the connection it receives comes from
 * the service, not from the caller — so whatever the service observed on the socket has
 * to travel with the event. `client_ip` continues to carry the forwarded address, but
 * only as description: a caller writes `X-Forwarded-For` itself, so anything that acted
 * on it would be acting on the caller's own say-so.
 */
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

const LOOPBACK = /^(?:::1|::ffff:127\.0\.0\.1|127\.0\.0\.1)$/;

async function settle(min = 1) {
  await client.flush();
  await collector.waitFor(() => collector.events.length >= min);
}

describe("peer_ip on request events", () => {
  it("reports the socket peer on every request event", async () => {
    await fetch(`${target.url}/api/orders/1002`);
    await settle();
    const event = collector.httpEvents()[0]!;
    expect(event.peer_ip).toMatch(LOOPBACK);
  });

  it("reports it on unmatched requests too, not just routed ones", async () => {
    await fetch(`${target.url}/does/not/exist`);
    await settle();
    expect(collector.httpEvents()[0]!.peer_ip).toMatch(LOOPBACK);
  });

  it("never takes it from a forwarded header, even with trust proxy on", async () => {
    const proxied = new TelemetryClient(
      { service: "shopfront", endpoint: collector.url, flushIntervalMs: 10 },
      {},
    );
    const app = express();
    app.set("trust proxy", true);
    app.use(telemetryMiddleware({ client: proxied }));
    app.get("/api/products", (_req, res) => {
      res.json({ ok: true });
    });
    const server = await listen(app);

    await fetch(`${server.url}/api/products`, {
      headers: { "x-forwarded-for": "10.1.2.3, 203.0.113.9" },
    });
    await proxied.flush();
    await collector.waitFor(() => collector.httpEvents().length > 0);
    const event = collector.httpEvents()[0]!;
    await server.close();
    await proxied.shutdown();

    // The forwarded value is still reported, because it is what an operator wants to
    // read; it simply is not what anything decides on.
    expect(event.client_ip).toBe("10.1.2.3");
    expect(event.peer_ip).toMatch(LOOPBACK);
    expect(event.peer_ip).not.toBe("10.1.2.3");
    expect(event.peer_ip).not.toBe("203.0.113.9");
  });

  it("agrees with the synthetic marker, both derived from the same read", async () => {
    const probeClient = new TelemetryClient(
      {
        service: "shopfront",
        endpoint: collector.url,
        flushIntervalMs: 10,
        syntheticCidrs: ["127.0.0.0/8", "::1/128"],
      },
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
    expect(event.peer_ip).toMatch(LOOPBACK);
  });
});

describe("peer_ip on events raised inside a handler", () => {
  /** An app whose handler raises a counter without being handed the request. */
  async function appRaisingSignal(instance: TelemetryClient) {
    const app = express();
    app.use(telemetryMiddleware({ client: instance }));
    app.get("/api/products", async (_req, res) => {
      // Deliberately after an await, so the assertion also covers context surviving
      // the microtask boundary an ordinary handler crosses.
      await new Promise((r) => setTimeout(r, 5));
      instance.signal("shop.catalog.query.plan_anomaly", { detail: "row set widened" });
      instance.correlate({
        signal: "shop.imports.fetch.egress",
        destinationHost: "supplier.example",
        route: "/api/products",
        param: "q",
      });
      res.json({ ok: true });
    });
    return listen(app);
  }

  it("inherits the peer from the request that provoked it", async () => {
    const server = await appRaisingSignal(client);
    await fetch(`${server.url}/api/products`);
    await settle(2);
    await collector.waitFor(() => collector.correlations.length === 1);
    await server.close();

    const signal = collector.events.find((e): e is SignalEvent => e.type === "signal")!;
    expect(signal.peer_ip).toMatch(LOOPBACK);
    expect(signal.synthetic).toBeUndefined();
    expect(collector.correlations[0]!.peer_ip).toMatch(LOOPBACK);
  });

  it("inherits the synthetic marker, so a probe's own counters are never counted", async () => {
    const probeClient = new TelemetryClient(
      {
        service: "shopfront",
        endpoint: collector.url,
        flushIntervalMs: 10,
        syntheticCidrs: ["127.0.0.0/8", "::1/128"],
      },
      {},
    );
    const server = await appRaisingSignal(probeClient);
    await fetch(`${server.url}/api/products`);
    await probeClient.flush();
    await collector.waitFor(
      () => collector.events.some((e) => e.type === "signal") && collector.correlations.length === 1,
    );
    await server.close();
    await probeClient.shutdown();

    // Without this the platform's own replay of a known-good input would be recorded
    // as a genuine anomaly, and every counter in the fleet would read high.
    const signal = collector.events.find((e): e is SignalEvent => e.type === "signal")!;
    expect(signal.synthetic).toBe(true);
    expect(collector.correlations[0]!.synthetic).toBe(true);
  });

  it("does not bleed between requests in flight at the same time", async () => {
    const plain = new TelemetryClient(
      { service: "shopfront", endpoint: collector.url, flushIntervalMs: 10 },
      {},
    );
    const probeClient = new TelemetryClient(
      {
        service: "shopfront",
        endpoint: collector.url,
        flushIntervalMs: 10,
        syntheticCidrs: ["127.0.0.0/8", "::1/128"],
      },
      {},
    );

    const slow = express();
    slow.use(telemetryMiddleware({ client: plain }));
    slow.get("/slow", async (_req, res) => {
      await new Promise((r) => setTimeout(r, 60));
      plain.signal("shop.catalog.slow.plan_anomaly");
      res.json({ ok: true });
    });
    const fast = express();
    fast.use(telemetryMiddleware({ client: probeClient }));
    fast.get("/fast", (_req, res) => {
      probeClient.signal("shop.catalog.fast.plan_anomaly");
      res.json({ ok: true });
    });

    const slowServer = await listen(slow);
    const fastServer = await listen(fast);
    // The fast request starts and finishes while the slow one is suspended.
    const slowRequest = fetch(`${slowServer.url}/slow`);
    await new Promise((r) => setTimeout(r, 10));
    await fetch(`${fastServer.url}/fast`);
    await slowRequest;

    await Promise.all([plain.flush(), probeClient.flush()]);
    await collector.waitFor(
      () => collector.events.filter((e) => e.type === "signal").length === 2,
    );
    await slowServer.close();
    await fastServer.close();
    await Promise.all([plain.shutdown(), probeClient.shutdown()]);

    const signals = collector.events.filter((e): e is SignalEvent => e.type === "signal");
    const byName = Object.fromEntries(signals.map((s) => [s.signal, s]));
    expect(byName["shop.catalog.fast.plan_anomaly"]?.synthetic).toBe(true);
    expect(byName["shop.catalog.slow.plan_anomaly"]?.synthetic).toBeUndefined();
  });
});

describe("peer_ip outside a request", () => {
  it("is omitted when there is no connection to attribute the event to", async () => {
    client.note("scheduled reconciliation finished");
    await settle();
    const note = collector.events.find((e) => e.type === "note")!;
    expect(note.peer_ip).toBeUndefined();
  });

  it("is taken from the caller for a websocket frame, which has no ambient context", async () => {
    client.websocket({
      route: "/ws/orders",
      message: '{"channel":"orders"}',
      clientIp: "10.1.2.3",
      peerIp: "192.0.2.7",
    });
    await settle();
    const event = collector.events.find((e): e is HttpRequestEvent => e.type === "http_request")!;
    expect(event.peer_ip).toBe("192.0.2.7");
    expect(event.client_ip).toBe("10.1.2.3");
  });
});
