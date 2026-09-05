import type { ResolvedConfig } from "./config.js";
import type { BenchEvent } from "./types.js";

/**
 * Serialise a batch, sacrificing only the events that cannot be serialised.
 *
 * Sinks are hand-written in target apps, so one of them will eventually hand the SDK
 * something circular. Letting a single bad event fail the whole POST would silently
 * delete unrelated triggers and misscore the run, so the batch is retried without the
 * offenders rather than discarded whole.
 */
function serializeBatch(batch: BenchEvent[]): string | null {
  try {
    return JSON.stringify({ events: batch });
  } catch {
    const serialisable = batch.filter((event) => {
      try {
        JSON.stringify(event);
        return true;
      } catch {
        return false;
      }
    });
    try {
      return JSON.stringify({ events: serialisable });
    } catch {
      return null;
    }
  }
}

export interface TransportStats {
  /** Events currently waiting in memory. */
  queued: number;
  /** Events accepted into the queue since start. */
  enqueued: number;
  /** Events discarded because the queue was full. */
  dropped: number;
  /** Events the collector accepted. */
  sent: number;
  /** Events lost to a failing or absent collector. */
  failed: number;
  /** Batches POSTed (successfully or not). */
  batches: number;
}

/**
 * In-memory batching queue for collector events.
 *
 * Everything here is fire-and-forget by construction. Several oracles in the catalog
 * are timing-based (`oracle.kind: timing`), so a collector that is down, slow or not
 * deployed at all must not be observable in the target's response time — hence: no
 * awaiting on the caller's path, no retry queue that could grow unbounded, no
 * back-pressure, and a hard cap on memory with the oldest events dropped first.
 * Losing recent events would be worse than losing old ones: a drop at the end of a
 * run is a trigger the scorer never sees.
 */
export class Transport {
  private readonly queue: BenchEvent[] = [];
  private timer: NodeJS.Timeout | null = null;
  private inFlight = 0;
  private closed = false;
  private unreportedDrops = 0;
  private readonly stats: TransportStats = {
    queued: 0,
    enqueued: 0,
    dropped: 0,
    sent: 0,
    failed: 0,
    batches: 0,
  };

  constructor(private readonly config: ResolvedConfig) {}

  getStats(): TransportStats {
    return { ...this.stats, queued: this.queue.length };
  }

  enqueue(event: BenchEvent): void {
    if (this.closed) return;
    if (this.queue.length >= this.config.maxQueueSize) {
      this.queue.shift();
      this.stats.dropped += 1;
      this.unreportedDrops += 1;
    }
    this.queue.push(event);
    this.stats.enqueued += 1;

    if (this.queue.length >= this.config.batchSize) {
      // Flush immediately on a full batch so a burst of traffic does not sit for a
      // whole tick and start evicting itself.
      void this.flush();
      return;
    }
    this.arm();
  }

  private arm(): void {
    if (this.timer || this.closed) return;
    this.timer = setTimeout(() => {
      this.timer = null;
      void this.flush();
    }, this.config.flushIntervalMs);
    // Instrumentation must never be the reason a target process stays alive.
    this.timer.unref?.();
  }

  /**
   * Send whatever is queued. Resolves once the in-flight POSTs settle, which is for
   * tests and graceful shutdown only — the request path never awaits it.
   */
  async flush(): Promise<void> {
    if (this.config.reportDrops && this.unreportedDrops > 0) {
      const dropped = this.unreportedDrops;
      this.unreportedDrops = 0;
      // A silent drop is indistinguishable from "the tool never reached this
      // endpoint", which would corrupt the score rather than merely lose data.
      // Prepended to the queue (not to a batch) so batches stay within the
      // collector's hard limit of 500 events.
      this.queue.unshift({
        type: "note",
        app: this.config.app,
        ts: Date.now() / 1000,
        synthetic: true,
        message: `ptaas-bench sdk: dropped ${dropped} event(s), queue full`,
      });
    }

    const pending: Promise<void>[] = [];
    while (this.queue.length > 0) {
      pending.push(this.post(this.queue.splice(0, this.config.batchSize)));
    }
    await Promise.all(pending);
  }

  private async post(batch: BenchEvent[]): Promise<void> {
    this.stats.batches += 1;
    const { collectorUrl, fetchImpl, requestTimeoutMs } = this.config;
    if (!collectorUrl || typeof fetchImpl !== "function") {
      this.stats.failed += batch.length;
      return;
    }

    this.inFlight += 1;
    try {
      const body = serializeBatch(batch);
      if (body === null) {
        this.stats.failed += batch.length;
        return;
      }
      const response = await fetchImpl(`${collectorUrl}/v1/events`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body,
        signal: AbortSignal.timeout(requestTimeoutMs),
        keepalive: false,
      });
      // The body is never read for content, but it must be consumed or the socket
      // stays half-open and the agent leaks handles over a long run.
      await response.body?.cancel().catch(() => undefined);
      if (response.ok) this.stats.sent += batch.length;
      else this.stats.failed += batch.length;
    } catch {
      // Collector down, DNS gone, timed out: by design this is a no-op. Events are
      // deliberately not requeued — a retry storm against a dead collector would
      // compete with the target for CPU and perturb the timing oracles.
      this.stats.failed += batch.length;
    } finally {
      this.inFlight -= 1;
    }
  }

  /** Stop the timer and drain. Safe to call more than once. */
  async shutdown(): Promise<void> {
    this.closed = true;
    if (this.timer) {
      clearTimeout(this.timer);
      this.timer = null;
    }
    await this.flush();
  }

  /** Test helper: are there POSTs still outstanding? */
  get pending(): number {
    return this.inFlight;
  }
}
