import { pbkdf2 } from "node:crypto";
import { Worker } from "node:worker_threads";
import express from "express";
import { afterAll, beforeAll, beforeEach, describe, expect, it } from "vitest";

import { TelemetryClient } from "../src/client.js";
import { bindContext } from "../src/context.js";
import { telemetryMiddleware } from "../src/middleware.js";
import type { SignalEvent } from "../src/types.js";
import { listen, type TestApp } from "./app-fixture.js";
import { FakeCollector } from "./fake-collector.js";

/**
 * Where request context survives, and where it does not.
 *
 * Context inheritance is what puts a peer address and a synthetic marker on a counter
 * raised deep inside a handler. Where it silently stops, events come back looking like
 * ordinary unattributed traffic rather than like missing data — which is the failure
 * mode that costs credit without anyone noticing. So the boundaries are pinned down by
 * tests rather than by assumption.
 */
const collector = new FakeCollector();
let client: TelemetryClient;

/** A pooled worker, built once at startup — the shape that actually loses context. */
let worker: Worker;
/**
 * A background drain loop started at process start, like any in-process job queue.
 *
 * The distinction that matters: a `setTimeout` scheduled *inside* a handler still
 * carries the request, because the timer is created in that context. It is only lost
 * when the thing that eventually invokes the callback was itself created outside a
 * request — which is exactly what a long-lived scheduler is.
 */
const queue: (() => void)[] = [];
let scheduler: NodeJS.Timeout;

beforeAll(async () => {
  const endpoint = await collector.start();
  client = new TelemetryClient(
    {
      service: "shopfront",
      endpoint,
      flushIntervalMs: 10,
      syntheticCidrs: ["127.0.0.0/8", "::1/128"],
    },
    {},
  );
  worker = new Worker(
    "const {parentPort}=require('worker_threads');parentPort.on('message',(m)=>parentPort.postMessage(m));",
    { eval: true },
  );
  scheduler = setInterval(() => {
    while (queue.length > 0) queue.shift()!();
  }, 5);
  scheduler.unref();
});

afterAll(async () => {
  clearInterval(scheduler);
  await client.shutdown();
  await worker.terminate();
  await collector.stop();
});

beforeEach(() => {
  collector.reset();
  queue.length = 0;
});

const LOOPBACK = /^(?:::1|::ffff:127\.0\.0\.1|127\.0\.0\.1)$/;

/** Serve one request through `handler` and return the signal it raised. */
async function signalRaisedBy(
  handler: (done: () => void) => void | Promise<void>,
): Promise<SignalEvent> {
  const app = express();
  app.use(telemetryMiddleware({ client }));
  app.get("/api/products", (_req, res) => {
    void handler(() => res.json({ ok: true }));
  });
  const server = await listen(app);
  await fetch(`${server.url}/api/products`);
  await client.flush();
  await collector.waitFor(() => collector.events.some((e) => e.type === "signal"));
  await server.close();
  return collector.events.find((e): e is SignalEvent => e.type === "signal")!;
}

const raise = (name: string) => client.signal(name);

describe("boundaries context crosses on its own", () => {
  it("survives promises, timers, nextTick, setImmediate and the libuv thread pool", async () => {
    const signal = await signalRaisedBy(async (done) => {
      await new Promise((r) => setTimeout(r, 1));
      await new Promise((r) => queueMicrotask(() => r(null)));
      await new Promise((r) => process.nextTick(() => r(null)));
      await new Promise((r) => setImmediate(() => r(null)));
      // Real work handed to the thread pool: crypto, zlib and fs all behave this way.
      await new Promise((r) => pbkdf2("p", "s", 16, 16, "sha256", () => r(null)));
      raise("shop.catalog.query.plan_anomaly");
      done();
    });

    expect(signal.peer_ip).toMatch(LOOPBACK);
    expect(signal.synthetic).toBe(true);
  });
});

describe("boundaries context does not cross", () => {
  it("is lost across a pooled worker thread, even when the listener is registered inside the request", async () => {
    const signal = await signalRaisedBy((done) => {
      worker.once("message", () => {
        raise("shop.catalog.worker.plan_anomaly");
        done();
      });
      worker.postMessage(1);
    });

    // A worker message callback runs in the context that existed when the worker was
    // constructed. For a pool built at startup, that is no context at all.
    expect(signal.peer_ip).toBeUndefined();
    expect(signal.synthetic).toBeUndefined();
  });

  it("is lost when a job is drained by a scheduler started outside the request", async () => {
    const signal = await signalRaisedBy((done) => {
      queue.push(() => raise("shop.catalog.queue.plan_anomaly"));
      done();
    });

    expect(signal.peer_ip).toBeUndefined();
    expect(signal.synthetic).toBeUndefined();
  });
});

describe("bindContext closes both gaps", () => {
  it("carries the peer and the synthetic marker across a worker thread", async () => {
    const signal = await signalRaisedBy((done) => {
      worker.once(
        "message",
        bindContext(() => {
          raise("shop.catalog.worker.plan_anomaly");
          done();
        }),
      );
      worker.postMessage(1);
    });

    expect(signal.peer_ip).toMatch(LOOPBACK);
    expect(signal.synthetic).toBe(true);
  });

  it("carries them across an externally drained queue", async () => {
    const signal = await signalRaisedBy((done) => {
      queue.push(bindContext(() => raise("shop.catalog.queue.plan_anomaly")));
      done();
    });

    expect(signal.peer_ip).toMatch(LOOPBACK);
    expect(signal.synthetic).toBe(true);
  });

  it("pins to no context when bound outside a request", async () => {
    // Binding at startup must not let the callback adopt whichever request happens to
    // be in flight when it finally runs: attributing an event to an unrelated caller is
    // worse than attributing it to nobody.
    const boundAtStartup = bindContext(() => raise("shop.catalog.startup.plan_anomaly"));
    const signal = await signalRaisedBy((done) => {
      boundAtStartup();
      done();
    });

    expect(signal.peer_ip).toBeUndefined();
    expect(signal.synthetic).toBeUndefined();
  });

  it("passes arguments and the return value through unchanged", () => {
    const bound = bindContext((a: number, b: string) => `${a}:${b}`);
    expect(bound(7, "x")).toBe("7:x");
    expect(() =>
      bindContext(() => {
        throw new Error("from the callback");
      })(),
    ).toThrow("from the callback");
  });
});
