import { AsyncLocalStorage } from "node:async_hooks";

/**
 * Ambient facts about the request currently being served.
 *
 * Application code raises a counter with one call and no plumbing — it does not, and
 * should not, have to thread the request object down to whatever raised it. Context
 * propagation through `AsyncLocalStorage` is how a telemetry client normally gets
 * request-scoped detail onto events emitted deep inside a handler.
 */
export interface RequestContext {
  /**
   * Address of the peer that opened the connection, as read from the socket.
   *
   * Never a value taken from a header. See `peerAddress` for why that distinction is
   * load-bearing rather than cosmetic.
   */
  peerIp?: string;
  /** Whether that peer belongs to the configured synthetic-monitoring ranges. */
  synthetic: boolean;
}

const storage = new AsyncLocalStorage<RequestContext>();

/**
 * Run `fn` with `context` in scope.
 *
 * The callback's return value and any exception it throws pass straight through, so
 * wrapping a middleware chain in this does not change how errors reach the
 * application's own error handler.
 */
export function runInContext<T>(context: RequestContext, fn: () => T): T {
  return storage.run(context, fn);
}

/** The context of the request being served, or undefined outside one. */
export function currentContext(): RequestContext | undefined {
  return storage.getStore();
}
