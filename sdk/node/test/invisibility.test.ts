import { EventEmitter } from "node:events";
import express, { type Express } from "express";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { Bench } from "../src/client.js";
import { benchMiddleware } from "../src/middleware.js";
import { BlackHoleServer, closedPortUrl, FakeCollector } from "./fake-collector.js";
import { buildApp, listen, type TestApp } from "./app-fixture.js";

/**
 * The benchmark is only meaningful if instrumentation is undetectable and free.
 *
 * Several catalog oracles are `kind: timing`, and a tool under test is free to
 * fingerprint the target. So: a collector that is down, hung or simply absent must
 * change neither the target's response time, nor its headers, nor its bodies, nor its
 * routing table, nor its logs.
 */

const blackHole = new BlackHoleServer();
let deadUrl: string;
let hungUrl: string;

beforeAll(async () => {
  hungUrl = await blackHole.start();
  deadUrl = await closedPortUrl();
});

afterAll(async () => {
  await blackHole.stop();
});

function median(values: number[]): number {
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid]! : (sorted[mid - 1]! + sorted[mid]!) / 2;
}

/** A request/response pair rich enough to exercise every extraction path. */
function mockExchange() {
  const res = new EventEmitter() as EventEmitter & {
    statusCode: number;
    end: (...args: unknown[]) => unknown;
  };
  res.statusCode = 200;
  res.end = () => res;
  const req = {
    method: "POST",
    url: "/api/orders/1002?q=laptop&sort=price",
    originalUrl: "/api/orders/1002?q=laptop&sort=price",
    baseUrl: "/api/orders",
    route: { path: "/:id" },
    params: { id: "1002" },
    query: { q: "laptop", sort: "price" },
    body: { source_url: "http://x", options: { retries: 3 } },
    headers: {
      host: "target.bench.local",
      "user-agent": "ZAP/2.15",
      "content-type": "application/json",
      cookie: "session=deadbeef; role=admin",
      "x-forwarded-for": "10.0.0.1",
      accept: "*/*",
    },
    socket: { remoteAddress: "10.0.0.1" },
  };
  return { req, res };
}

describe("cost on the response path", () => {
  it("the middleware call itself costs well under 1ms, with the collector black-holed", async () => {
    const bench = new Bench({ app: "shopfront", collectorUrl: hungUrl, requestTimeoutMs: 50 }, {});
    const middleware = benchMiddleware({ bench });

    // Warm up so JIT compilation is not billed to the measurement.
    for (let i = 0; i < 2_000; i += 1) {
      const { req, res } = mockExchange();
      middleware(req, res, () => undefined);
    }

    const iterations = 5_000;
    const started = performance.now();
    for (let i = 0; i < iterations; i += 1) {
      const { req, res } = mockExchange();
      middleware(req, res, () => undefined);
    }
    const perCallMs = (performance.now() - started) / iterations;

    expect(perCallMs).toBeLessThan(1);
    await bench.shutdown();
  });

  it("adds under 1ms to a real request, even when the collector never answers", async () => {
    const bench = new Bench({ app: "shopfront", collectorUrl: hungUrl, requestTimeoutMs: 50 }, {});

    const bare: Express = express();
    bare.get("/api/orders/:id", (req, res) => {
      res.json({ id: req.params.id });
    });

    const instrumented = await listen(buildApp(bench));
    const control = await listen(bare);

    const warm = 50;
    const rounds = 250;
    const instrumentedTimes: number[] = [];
    const controlTimes: number[] = [];

    // Interleaved rather than sequential: measuring one app fully and then the other
    // would attribute any drift in the machine's load to the instrumentation.
    for (let i = 0; i < warm + rounds; i += 1) {
      for (const [target, times] of [
        [instrumented, instrumentedTimes],
        [control, controlTimes],
      ] as const) {
        const t0 = performance.now();
        await fetch(`${target.url}/api/orders/1002`);
        const elapsed = performance.now() - t0;
        if (i >= warm) times.push(elapsed);
      }
    }

    const delta = median(instrumentedTimes) - median(controlTimes);
    expect(delta).toBeLessThan(1);

    await instrumented.close();
    await control.close();
    await bench.shutdown();
  });

  it("does not block on a collector that answers very slowly", async () => {
    const slow = new FakeCollector();
    const url = await slow.start();
    slow.responseDelayMs = 2_000;

    const bench = new Bench({ app: "shopfront", collectorUrl: url, flushIntervalMs: 10, requestTimeoutMs: 300 }, {});
    const target = await listen(buildApp(bench));

    const t0 = performance.now();
    for (let i = 0; i < 20; i += 1) await fetch(`${target.url}/api/orders/${i}`);
    const elapsed = performance.now() - t0;

    // 20 round trips must not have waited on a single 2s collector response.
    expect(elapsed).toBeLessThan(1_000);

    await target.close();
    await bench.shutdown();
    await slow.stop();
  });
});

describe("failure containment", () => {
  it("never throws and always calls next(), whatever the request looks like", () => {
    const bench = new Bench({ app: "shopfront", collectorUrl: deadUrl }, {});
    const middleware = benchMiddleware({ bench });

    const hostile: [unknown, unknown][] = [
      [{}, {}],
      [null, null],
      [Object.freeze({ method: "GET", url: "/x", headers: {} }), {}],
      [
        {
          method: "GET",
          url: "/x",
          get headers(): never {
            throw new Error("hostile getter");
          },
        },
        {
          get statusCode(): never {
            throw new Error("hostile getter");
          },
        },
      ],
      [{ method: "GET", url: "/x", headers: { cookie: "%%%=%%%" }, body: undefined }, { end: 1 }],
    ];

    for (const [req, res] of hostile) {
      let called = 0;
      expect(() => middleware(req, res, () => { called += 1; })).not.toThrow();
      expect(called).toBe(1);
    }
  });

  it("propagates no rejection when the collector is gone", async () => {
    const rejections: unknown[] = [];
    const onRejection = (reason: unknown) => rejections.push(reason);
    process.on("unhandledRejection", onRejection);

    const bench = new Bench({ app: "shopfront", collectorUrl: deadUrl, flushIntervalMs: 10 }, {});
    const target = await listen(buildApp(bench));
    for (let i = 0; i < 10; i += 1) {
      const response = await fetch(`${target.url}/api/orders/${i}`);
      expect(response.status).toBe(200);
    }
    // Give the background timer time to fire and fail.
    await new Promise((r) => setTimeout(r, 120));
    await bench.flush();
    await new Promise((r) => setTimeout(r, 20));

    process.off("unhandledRejection", onRejection);
    expect(rejections).toEqual([]);
    expect(bench.stats().failed).toBeGreaterThan(0);

    await target.close();
    await bench.shutdown();
  });

  it("does not let a downstream error be swallowed or double-handled", async () => {
    const bench = new Bench({ app: "shopfront", collectorUrl: deadUrl }, {});
    const app = express();
    app.use(benchMiddleware({ bench }));
    app.get("/boom", () => {
      throw new Error("target error");
    });
    let handled = 0;
    app.use((err: unknown, _req: unknown, res: express.Response, _next: express.NextFunction) => {
      handled += 1;
      res.status(500).json({ error: (err as Error).message });
    });
    const target = await listen(app);

    const response = await fetch(`${target.url}/boom`);
    expect(response.status).toBe(500);
    // The target's own error handler ran exactly once, and the body is untouched.
    expect(handled).toBe(1);
    expect(await response.json()).toEqual({ error: "target error" });

    await target.close();
    await bench.shutdown();
  });
});

describe("undetectability", () => {
  let bench: Bench;
  let instrumented: TestApp;
  let control: TestApp;

  beforeAll(async () => {
    bench = new Bench({ app: "shopfront", collectorUrl: deadUrl }, {});
    instrumented = await listen(buildApp(bench));
    const bare = express();
    bare.use(express.json());
    bare.get("/api/products", (req, res) => {
      res.json({ q: req.query.q ?? null });
    });
    bare.use((_req, res) => {
      res.status(404).json({ error: "not found" });
    });
    control = await listen(bare);
  });

  afterAll(async () => {
    await instrumented.close();
    await control.close();
    await bench.shutdown();
  });

  it("adds no response header, on any status", async () => {
    for (const path of ["/api/products?q=laptop", "/nope"]) {
      const a = await fetch(`${instrumented.url}${path}`);
      const b = await fetch(`${control.url}${path}`);
      const names = (r: Response) =>
        [...r.headers.keys()].filter((h) => h !== "date" && h !== "etag" && h !== "content-length").sort();
      expect(names(a)).toEqual(names(b));
      expect(await a.text()).toBe(await b.text());
    }
  });

  it("adds no route: an unknown path still 404s with the target's own body", async () => {
    for (const probe of ["/__bench", "/bench", "/.bench/events", "/metrics", "/healthz"]) {
      const response = await fetch(`${instrumented.url}${probe}`);
      expect(response.status).toBe(404);
      expect(await response.json()).toEqual({ error: "not found" });
    }
  });

  it("writes nothing to stdout or stderr on the response path", async () => {
    const captured: string[] = [];
    const patch = (stream: NodeJS.WriteStream) => {
      const original = stream.write.bind(stream);
      stream.write = ((chunk: string | Uint8Array, ...rest: unknown[]) => {
        captured.push(chunk.toString());
        return (original as (...a: unknown[]) => boolean)(chunk, ...rest);
      }) as typeof stream.write;
      return () => {
        stream.write = original;
      };
    };
    const restore = [patch(process.stdout), patch(process.stderr)];
    try {
      await fetch(`${instrumented.url}/api/products?q=laptop`);
      await fetch(`${instrumented.url}/nope`);
      await new Promise((r) => setTimeout(r, 50));
    } finally {
      for (const undo of restore) undo();
    }
    expect(captured.filter((line) => /bench|collector|ECONNREFUSED/i.test(line))).toEqual([]);
  });

  it("leaves no enumerable trace on the request object for the app to trip over", async () => {
    const seen: string[][] = [];
    const app = express();
    app.use(benchMiddleware({ bench }));
    app.get("/probe", (req, res) => {
      seen.push(Object.keys(req));
      res.json({ ok: true });
    });
    const probe = await listen(app);
    await fetch(`${probe.url}/probe`);
    await probe.close();

    // The per-request state hangs off a symbol, and `route` keeps its normal shape.
    expect(seen[0]?.some((k) => k.toLowerCase().includes("bench"))).toBe(false);
  });
});
