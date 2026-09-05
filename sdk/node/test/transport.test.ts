import { afterEach, describe, expect, it } from "vitest";

import { Bench } from "../src/client.js";
import type { NoteEvent } from "../src/types.js";
import { closedPortUrl, FakeCollector } from "./fake-collector.js";

const started: Bench[] = [];
const collectors: FakeCollector[] = [];

function makeBench(collectorUrl: string, overrides: Partial<ConstructorParameters<typeof Bench>[0]> = {}) {
  const bench = new Bench({ app: "shopfront", collectorUrl, ...overrides }, {});
  started.push(bench);
  return bench;
}

async function makeCollector() {
  const collector = new FakeCollector();
  collectors.push(collector);
  return { collector, url: await collector.start() };
}

afterEach(async () => {
  await Promise.all(started.splice(0).map((b) => b.shutdown()));
  await Promise.all(collectors.splice(0).map((c) => c.stop()));
});

describe("batching and flushing", () => {
  it("flushes on the background timer without any caller awaiting it", async () => {
    const { collector, url } = await makeCollector();
    const bench = makeBench(url, { flushIntervalMs: 60 });

    bench.note("seeded");
    // Nothing was awaited: the event is still only in memory.
    expect(collector.events).toHaveLength(0);
    expect(bench.stats().queued).toBe(1);

    await collector.waitFor(() => collector.events.length === 1);
    expect(collector.batches).toHaveLength(1);
    expect((collector.events[0] as NoteEvent).message).toBe("seeded");
  });

  it("stamps app and timestamp on every event", async () => {
    const { collector, url } = await makeCollector();
    const bench = makeBench(url, { flushIntervalMs: 10 });
    const before = Date.now() / 1000;
    bench.note("hello");
    await bench.flush();
    await collector.waitFor(() => collector.events.length === 1);

    const event = collector.events[0]!;
    expect(event.app).toBe("shopfront");
    expect(event.ts).toBeGreaterThanOrEqual(before - 1);
  });

  it("splits into batches of at most batchSize and flushes immediately when full", async () => {
    const { collector, url } = await makeCollector();
    // Long timer: if batching on a full batch did not work, nothing would arrive.
    const bench = makeBench(url, { batchSize: 10, flushIntervalMs: 60_000 });

    for (let i = 0; i < 25; i += 1) bench.note(`n${i}`);
    await collector.waitFor(() => collector.events.length >= 20);

    expect(collector.batches.every((b) => b.events.length <= 10)).toBe(true);
    // 25 events with batchSize 10: two full batches went out, five wait for the timer.
    expect(collector.events).toHaveLength(20);
    expect(bench.stats().queued).toBe(5);
  });

  it("never exceeds the collector's hard limit of 500 events per batch", async () => {
    const { collector, url } = await makeCollector();
    const bench = makeBench(url, { flushIntervalMs: 60_000 });
    for (let i = 0; i < 1200; i += 1) bench.note(`n${i}`);
    await bench.flush();
    await collector.waitFor(() => collector.events.length === 1200);
    expect(Math.max(...collector.batches.map((b) => b.events.length))).toBeLessThanOrEqual(500);
  });
});

describe("bounded queue", () => {
  it("drops the oldest events and counts them", async () => {
    const { collector, url } = await makeCollector();
    const bench = makeBench(url, { maxQueueSize: 5, batchSize: 1000, flushIntervalMs: 60_000 });

    for (let i = 0; i < 12; i += 1) bench.note(`n${i}`);

    const stats = bench.stats();
    expect(stats.queued).toBe(5);
    expect(stats.enqueued).toBe(12);
    expect(stats.dropped).toBe(7);

    await bench.flush();
    await collector.waitFor(() => collector.events.length >= 6);

    const messages = collector.events
      .filter((e): e is NoteEvent => e.type === "note")
      .map((e) => e.message ?? "");
    // The newest events survive: a drop at the end of a run is a trigger the scorer
    // would never see, which matters far more than losing early traffic.
    expect(messages).toContain("n11");
    expect(messages).toContain("n7");
    expect(messages).not.toContain("n0");
  });

  it("reports the drop count to the collector so a lossy run is not read as a low score", async () => {
    const { collector, url } = await makeCollector();
    const bench = makeBench(url, { maxQueueSize: 2, flushIntervalMs: 60_000 });
    for (let i = 0; i < 6; i += 1) bench.note(`n${i}`);
    await bench.flush();
    await collector.waitFor(() => collector.events.length >= 3);

    const drops = collector.events.find(
      (e): e is NoteEvent => e.type === "note" && (e.message ?? "").includes("dropped"),
    );
    expect(drops?.message).toContain("dropped 4 event(s)");
    // Its own bookkeeping is platform traffic, never a tool's doing.
    expect(drops?.synthetic).toBe(true);
  });

  it("can be silenced", async () => {
    const { collector, url } = await makeCollector();
    const bench = makeBench(url, { maxQueueSize: 2, flushIntervalMs: 60_000, reportDrops: false });
    for (let i = 0; i < 6; i += 1) bench.note(`n${i}`);
    await bench.flush();
    await collector.waitFor(() => collector.events.length >= 2);
    expect(collector.events.some((e) => (e as NoteEvent).message?.includes("dropped"))).toBe(false);
    expect(bench.stats().dropped).toBe(4);
  });
});

describe("collector failures", () => {
  it("counts events lost to a refused connection and keeps accepting more", async () => {
    const bench = makeBench(await closedPortUrl(), { flushIntervalMs: 10 });
    bench.note("one");
    await bench.flush();
    expect(bench.stats().failed).toBe(1);

    // Crucially, the queue is not re-filled with the failure: a retry storm against a
    // dead collector would compete with the target for CPU.
    expect(bench.stats().queued).toBe(0);
    bench.note("two");
    expect(bench.stats().queued).toBe(1);
  });

  it("counts a rejected batch as failed without throwing", async () => {
    const { collector, url } = await makeCollector();
    collector.status = 500;
    const bench = makeBench(url, { flushIntervalMs: 10 });
    bench.note("one");
    await expect(bench.flush()).resolves.toBeUndefined();
    expect(bench.stats().failed).toBe(1);
  });

  it("loses only the unserialisable event, not the whole batch", async () => {
    const { collector, url } = await makeCollector();
    const bench = makeBench(url, { flushIntervalMs: 60_000 });

    const circular: Record<string, unknown> = {};
    circular.self = circular;
    bench.note("before");
    // A hand-written sink in a target app will eventually hand the SDK something like
    // this; it must not delete the triggers queued around it.
    bench.emit({ type: "note", app: "shopfront", message: circular as unknown as string });
    bench.note("after");

    await bench.flush();
    await collector.waitFor(() => collector.events.length >= 2);
    const messages = collector.events.map((e) => (e as NoteEvent).message);
    expect(messages).toContain("before");
    expect(messages).toContain("after");
  });

  it("is inert when disabled, and never queues", async () => {
    const { collector, url } = await makeCollector();
    const bench = makeBench(url, { enabled: false });
    bench.note("ignored");
    bench.trigger("BENCH-SHOP-0001", { oracleKind: "sink" });
    await bench.flush();
    expect(bench.stats().enqueued).toBe(0);
    expect(collector.events).toHaveLength(0);
  });
});
