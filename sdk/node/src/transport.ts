import type { ResolvedConfig } from "./config.js";
import type { EgressCorrelation, TelemetryEvent } from "./types.js";

export interface TransportStats {
  /** Events currently waiting in memory. */
  queued: number;
  /** Events accepted into the queue since start. */
  enqueued: number;
  /** Events discarded because the queue was full. */
  discarded: number;
  /** Events the backend accepted. */
  sent: number;
  /** Events lost to an unreachable or failing backend. */
  failed: number;
  /** Batches POSTed, successfully or not. */
  batches: number;
}

/**
 * Serialise a batch, sacrificing only the events that cannot be serialised.
 *
 * Application code eventually hands the client something circular. Letting one bad
 * value fail the whole POST would silently delete unrelated events, so the batch is
 * retried without the offenders rather than discarded whole.
 */
function serializeBatch(batch: TelemetryEvent[]): string | null {
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

/**
 * In-memory batching queue.
 *
 * Everything here is fire-and-forget by construction. Telemetry must never be visible
 * in an endpoint's response time — latency histograms are the whole point of collecting
 * it, and a client that perturbs them measures itself. Hence: nothing awaited on the
 * caller's path, no retry queue that could grow without bound, no back-pressure, and a
 * hard cap on memory. When that cap is hit the oldest events go first: during an
 * incident the most recent ones are the ones somebody is looking at.
 */
export class Transport {
  private readonly queue: TelemetryEvent[] = [];
  private timer: NodeJS.Timeout | null = null;
  private inFlight = 0;
  private closed = false;
  private unreportedDiscards = 0;
  private readonly stats: TransportStats = {
    queued: 0,
    enqueued: 0,
    discarded: 0,
    sent: 0,
    failed: 0,
    batches: 0,
  };

  constructor(private readonly config: ResolvedConfig) {}

  getStats(): TransportStats {
    return { ...this.stats, queued: this.queue.length };
  }

  enqueue(event: TelemetryEvent): void {
    if (this.closed) return;
    if (this.queue.length >= this.config.maxQueueSize) {
      this.queue.shift();
      this.stats.discarded += 1;
      this.unreportedDiscards += 1;
    }
    this.queue.push(event);
    this.stats.enqueued += 1;

    if (this.queue.length >= this.config.batchSize) {
      // Send immediately on a full batch, so a traffic burst does not sit for a whole
      // tick and start evicting itself.
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
    // Telemetry must never be the reason a process refuses to exit.
    this.timer.unref?.();
  }

  /**
   * Send whatever is queued. The returned promise settles once the in-flight POSTs do,
   * which is for shutdown hooks and tests only; the request path never awaits it.
   */
  async flush(): Promise<void> {
    if (this.config.reportDiscards && this.unreportedDiscards > 0) {
      const discarded = this.unreportedDiscards;
      this.unreportedDiscards = 0;
      // Prepended to the queue rather than to a batch, so batches stay within the
      // ingest limit of 500.
      this.queue.unshift({
        type: "note",
        app: this.config.service,
        ts: Date.now() / 1000,
        synthetic: true,
        message: `telemetry: discarded ${discarded} event(s), queue limit reached`,
      });
    }

    const pending: Promise<void>[] = [];
    while (this.queue.length > 0) {
      pending.push(this.post(this.queue.splice(0, this.config.batchSize)));
    }
    await Promise.all(pending);
  }

  private async post(batch: TelemetryEvent[]): Promise<void> {
    this.stats.batches += 1;
    const { endpoint, eventsPath } = this.config;
    const body = endpoint ? serializeBatch(batch) : null;
    if (body === null) {
      this.stats.failed += batch.length;
      return;
    }
    const ok = await this.send(`${endpoint}${eventsPath}`, body);
    if (ok) this.stats.sent += batch.length;
    else this.stats.failed += batch.length;
  }

  /**
   * Declare an outbound destination, out of band from the event batch.
   *
   * Sent on its own rather than queued: the correlation has to reach the collector
   * around the same time as the outbound request it describes, and waiting for the next
   * flush tick would routinely be too late. Still never awaited by the caller.
   */
  dispatchCorrelation(correlation: EgressCorrelation): void {
    if (this.closed) return;
    const { endpoint, correlationsPath } = this.config;
    if (!endpoint) return;
    let body: string;
    try {
      body = JSON.stringify(correlation);
    } catch {
      return;
    }
    void this.send(`${endpoint}${correlationsPath}`, body);
  }

  private async send(url: string, body: string): Promise<boolean> {
    const { fetchImpl, requestTimeoutMs } = this.config;
    if (typeof fetchImpl !== "function") return false;

    this.inFlight += 1;
    try {
      const response = await fetchImpl(url, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body,
        signal: AbortSignal.timeout(requestTimeoutMs),
        keepalive: false,
      });
      // The body is never read for content, but it has to be consumed or the socket
      // stays half-open and handles leak over a long uptime.
      await response.body?.cancel().catch(() => undefined);
      return response.ok;
    } catch {
      // Backend down, DNS gone, timed out: by design a no-op. Nothing is requeued —
      // a retry storm against a dead backend would compete with the service for CPU.
      return false;
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

  /** Outstanding POSTs, for tests. */
  get pending(): number {
    return this.inFlight;
  }
}
