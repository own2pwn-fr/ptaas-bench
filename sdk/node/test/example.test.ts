import { afterAll, beforeAll, beforeEach, describe, expect, it } from "vitest";

import type { HttpRequestEvent, TriggerEvent } from "../src/types.js";
import { listen, type TestApp } from "./app-fixture.js";
import { FakeCollector } from "./fake-collector.js";

/**
 * Smoke test for examples/express-minimal.ts.
 *
 * The example is the template every target application copies, so it is worth proving
 * that the pattern actually works end to end: a benign query scores reach and
 * exercise only, a real UNION payload fires the sink.
 */
const collector = new FakeCollector();
let target: TestApp;
let bench: { flush(): Promise<void>; shutdown(): Promise<void> };

beforeAll(async () => {
  const collectorUrl = await collector.start();
  // The example calls initBench() at module scope, exactly as a target would, so the
  // environment has to be in place before it is imported.
  process.env.BENCH_APP = "shopfront";
  process.env.BENCH_COLLECTOR_URL = collectorUrl;

  const sdk = await import("../src/index.js");
  const example = await import("../examples/express-minimal.js");
  bench = sdk.getBench();
  target = await listen(example.app);
});

afterAll(async () => {
  await bench.shutdown();
  await target.close();
  await collector.stop();
  delete process.env.BENCH_APP;
  delete process.env.BENCH_COLLECTOR_URL;
});

beforeEach(() => collector.reset());

async function drive(url: string) {
  const response = await fetch(`${target.url}${url}`);
  const body = await response.json();
  await bench.flush();
  await collector.waitFor(() => collector.events.length > 0);
  return {
    body: body as { products?: unknown[] },
    http: collector.events.filter((e): e is HttpRequestEvent => e.type === "http_request"),
    triggers: collector.events.filter((e): e is TriggerEvent => e.type === "trigger"),
  };
}

describe("examples/express-minimal", () => {
  it("reports the entrypoint of BENCH-SHOP-0001 as the catalog spells it", async () => {
    const { http, triggers } = await drive(`/api/products?q=${encodeURIComponent("laptop")}`);
    expect(http[0]?.route).toBe("/api/products");
    expect(http[0]?.params?.some((p) => p.name === "q" && p.in === "query")).toBe(true);
    // Visiting the parameter with its default value is reach, never trigger.
    expect(triggers).toHaveLength(0);
  });

  it("does not fire on a reflected quote alone", async () => {
    const { triggers } = await drive(`/api/products?q=${encodeURIComponent("laptop'")}`);
    expect(triggers).toHaveLength(0);
  });

  it("fires the sink oracle on the catalog's proof of concept", async () => {
    const poc = "x' UNION SELECT id,email,password_hash,1,1 FROM users--";
    const { body, triggers } = await drive(`/api/products?q=${encodeURIComponent(poc)}`);

    // The flaw is real: another table's rows genuinely came back.
    expect(JSON.stringify(body)).toContain("ada@example.com");
    expect(triggers).toHaveLength(1);
    expect(triggers[0]).toMatchObject({
      vuln_id: "BENCH-SHOP-0001",
      oracle_kind: "sink",
      app: "shopfront",
    });
    expect(triggers[0]?.evidence?.payload).toBe(poc);
    expect(triggers[0]?.evidence?.detail).toContain("users");
  });

  it("reports an unmatched probe with the real path", async () => {
    const { http } = await drive("/wp-admin/setup-config.php");
    expect(http[0]?.route).toBe("<unmatched>");
    expect(http[0]?.path).toBe("/wp-admin/setup-config.php");
    expect(http[0]?.status).toBe(404);
  });
});
