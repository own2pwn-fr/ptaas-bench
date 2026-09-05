import { createServer, type Server } from "node:http";
import { createServer as createSocketServer, type Server as SocketServer } from "node:net";
import type { AddressInfo } from "node:net";

import type { BenchEvent, HttpRequestEvent } from "../src/types.js";

export interface CollectorBatch {
  events: BenchEvent[];
  receivedAt: number;
}

/**
 * In-process stand-in for platform/collector.
 *
 * Speaks just enough of the frozen OpenAPI contract for the SDK to be exercised over
 * a real socket: tests assert on what was actually serialised and POSTed, not on the
 * SDK's own idea of what it queued.
 */
export class FakeCollector {
  readonly batches: CollectorBatch[] = [];
  private server: Server | null = null;
  private port = 0;
  /** Delay applied to every response, to simulate a slow collector. */
  responseDelayMs = 0;
  /** Status returned to the SDK; 500 exercises the failure path. */
  status = 202;

  async start(): Promise<string> {
    this.server = createServer((req, res) => {
      const chunks: Buffer[] = [];
      req.on("data", (c: Buffer) => chunks.push(c));
      req.on("end", () => {
        if (req.url === "/v1/events" && req.method === "POST") {
          try {
            const parsed = JSON.parse(Buffer.concat(chunks).toString("utf8")) as { events: BenchEvent[] };
            this.batches.push({ events: parsed.events, receivedAt: Date.now() });
          } catch {
            /* a malformed batch is a test failure elsewhere */
          }
        }
        const reply = () => {
          res.writeHead(this.status, { "content-type": "application/json" });
          res.end("{}");
        };
        if (this.responseDelayMs > 0) setTimeout(reply, this.responseDelayMs);
        else reply();
      });
    });
    await new Promise<void>((resolve) => this.server!.listen(0, "127.0.0.1", resolve));
    this.port = (this.server!.address() as AddressInfo).port;
    return this.url;
  }

  get url(): string {
    return `http://127.0.0.1:${this.port}`;
  }

  /** Every event received so far, flattened across batches. */
  get events(): BenchEvent[] {
    return this.batches.flatMap((b) => b.events);
  }

  httpEvents(): HttpRequestEvent[] {
    return this.events.filter((e): e is HttpRequestEvent => e.type === "http_request");
  }

  reset(): void {
    this.batches.length = 0;
  }

  /** Poll until `predicate` holds, so tests never race the 250ms flush timer. */
  async waitFor(predicate: () => boolean, timeoutMs = 5_000): Promise<void> {
    const deadline = Date.now() + timeoutMs;
    while (!predicate()) {
      if (Date.now() > deadline) throw new Error("timed out waiting for collector events");
      await new Promise((r) => setTimeout(r, 5));
    }
  }

  async stop(): Promise<void> {
    if (!this.server) return;
    await new Promise<void>((resolve) => this.server!.close(() => resolve()));
    this.server = null;
  }
}

/**
 * A socket that accepts connections and then says nothing, ever.
 *
 * "Collector is down" has two flavours and they fail differently: connection refused
 * (fast) and connection accepted but hung (slow). The second is the dangerous one for
 * a naive implementation, so it gets its own fixture.
 */
export class BlackHoleServer {
  private server: SocketServer | null = null;
  private port = 0;

  async start(): Promise<string> {
    this.server = createSocketServer((socket) => {
      // Hold the connection open and never reply.
      socket.on("error", () => undefined);
    });
    await new Promise<void>((resolve) => this.server!.listen(0, "127.0.0.1", resolve));
    this.port = (this.server!.address() as AddressInfo).port;
    return `http://127.0.0.1:${this.port}`;
  }

  async stop(): Promise<void> {
    if (!this.server) return;
    this.server.close();
    this.server = null;
  }
}

/** A port nothing listens on: connections are refused immediately. */
export async function closedPortUrl(): Promise<string> {
  const server = createSocketServer();
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const port = (server.address() as AddressInfo).port;
  await new Promise<void>((resolve) => server.close(() => resolve()));
  return `http://127.0.0.1:${port}`;
}
