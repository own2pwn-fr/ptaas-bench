/**
 * Application anomaly counters.
 *
 * The storefront reports two kinds of thing to the observability stack: one request
 * event per request, emitted by the telemetry middleware, and the counters below, which
 * the service raises when it notices something it did not expect to be able to happen.
 *
 * The rule for every counter here is the same, and it is the reason the on-call rota
 * tolerates them: increment on the confirmed effect, never on the suspicious input. A
 * counter that also counts inputs which turned out to be inert is dominated by noise
 * within a day and stops being usable as an alert, so each helper takes the observation
 * *after the fact* and decides from that.
 */
import { telemetry } from "@internal/telemetry";

export const COUNTERS = {
  catalogPlanAnomaly: "shop.catalog.query.plan_anomaly",
  catalogSortFault: "shop.catalog.sort.expression_fault",
  graphProductsPlanAnomaly: "shop.graph.products.plan_anomaly",
  savedSearchPlanAnomaly: "shop.search.saved.plan_anomaly",
  graphSchemaWalk: "shop.graph.schema.full_walk",
  graphBatchAmplification: "shop.graph.batch.amplification",
  reviewMarkupPersisted: "shop.reviews.body.markup_persisted",
  supportMarkupPersisted: "shop.support.thread.markup_persisted",
  searchScriptExecution: "shop.web.search.script_execution",
  accountScriptExecution: "shop.web.account.script_execution",
  couponActorRole: "shop.admin.coupons.actor_role_mismatch",
  transitionActorRole: "shop.orders.transition.actor_role_mismatch",
  corsCredentialedReflection: "shop.web.cors.credentialed_reflection",
  orderSubjectMismatch: "shop.orders.subject.mismatch",
  ticketSubjectMismatch: "shop.support.ticket.subject_mismatch",
  tokenUnverifiedAccept: "shop.auth.token.unverified_accept",
  tokenKeyPathEscape: "shop.auth.token.key_path_escape",
  stepUpUnverifiedGrant: "shop.auth.stepup.unverified_grant",
  loginCredentialSweep: "shop.auth.login.credential_sweep",
  stepUpCodeSweep: "shop.auth.stepup.code_sweep",
  cartStateDecode: "shop.cart.state.decode_anomaly",
  cartMergePrototype: "shop.cart.merge.prototype_write",
  graphVariablesPrototype: "shop.graph.variables.prototype_write",
  linePriceAuthority: "shop.checkout.line.price_authority",
  shippingRateAuthority: "shop.checkout.shipping.rate_authority",
  lineNegativeQuantity: "shop.checkout.line.negative_quantity",
  totalNumericOverflow: "shop.checkout.total.numeric_overflow",
  couponRedemptionExcess: "shop.checkout.coupon.redemption_excess",
  walletDoubleSpend: "shop.wallet.redemption.double_spend",
  streamCrossOrigin: "shop.stream.orders.cross_origin_session",
  importFetchExternal: "shop.imports.fetch.external",
  avatarFetchExternal: "shop.media.avatar.fetch_external",
};

/**
 * Raise a counter.
 *
 * `payload` is the input that produced the anomaly and `detail` is what was actually
 * observed, in a form the person reading the alert can act on without opening a shell.
 */
export function raise(counter, { payload, detail, requestId } = {}) {
  telemetry.signal(counter, { payload, detail, requestId });
}

/**
 * Declare an outbound request whose destination came from a caller.
 *
 * The resolver logs every lookup this process makes, but a log line on its own cannot
 * say which request caused it; declaring the destination first is what joins the two.
 * Returns the correlation id so the caller can hand it back to the client and to the
 * job that retries the fetch.
 */
export function declareEgress({ counter, destinationHost, route, param, requestId }) {
  return telemetry.correlate({
    signal: counter,
    destinationHost,
    route,
    param,
    requestId,
  });
}

/**
 * Once-per-window de-duplication.
 *
 * Several counters describe a sustained condition rather than a single event (a sweep
 * against one account, a run of identical failures). Without this, a five minute sweep
 * would raise several thousand identical counters and the alert would be useless.
 */
const seen = new Map();

export function firstInWindow(key, windowMs = 60_000) {
  const now = Date.now();
  const previous = seen.get(key);
  if (previous !== undefined && now - previous < windowMs) return false;
  seen.set(key, now);
  if (seen.size > 4096) {
    for (const [k, at] of seen) if (now - at > windowMs) seen.delete(k);
  }
  return true;
}

/** Drop the de-duplication state. Called by the reset path, never by a request. */
export function clearWindows() {
  seen.clear();
}
