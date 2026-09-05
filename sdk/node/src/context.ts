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

/**
 * Pin a callback to the context in scope right now.
 *
 * Context follows ordinary asynchrony on its own — promises, timers, `nextTick`,
 * `setImmediate` and work done on the libuv thread pool (`crypto.pbkdf2`, `zlib`, `fs`)
 * all carry it. Two boundaries do not:
 *
 *   - a `worker_threads` message callback, which runs in the context that existed when
 *     the worker was constructed, not the one that sent the message — so a pooled
 *     worker created at startup loses it even when the listener is registered inside a
 *     request;
 *   - a listener on a long-lived emitter, when whatever calls `emit()` is itself
 *     outside a request (a queue drain, a scheduler tick).
 *
 * Cross either without binding and events raised on the far side arrive with no peer
 * address and no synthetic marker, which reads as ordinary traffic rather than as
 * missing data. Wrap the callback at the point where the context still exists:
 *
 * ```ts
 * worker.once("message", bindContext((result) => {
 *   telemetry.signal("shop.catalog.query.plan_anomaly", { detail: result.reason });
 * }));
 * ```
 *
 * Binding outside any request pins the callback to *no* context, rather than letting it
 * pick up whichever request happens to be in scope when it eventually runs. Inheriting
 * an unrelated request would be worse than inheriting nothing: it attributes the event
 * to a caller who had nothing to do with it.
 */
export function bindContext<A extends unknown[], R>(fn: (...args: A) => R): (...args: A) => R {
  const captured = storage.getStore();
  if (captured === undefined) {
    return (...args: A): R => storage.exit(() => fn(...args));
  }
  return (...args: A): R => storage.run(captured, () => fn(...args));
}
