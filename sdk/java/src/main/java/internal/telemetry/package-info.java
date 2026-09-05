/**
 * Request records, application signals and dependency links for internal services.
 *
 * <p>Three properties the agent must not lose, in the order they get broken:
 *
 * <ol>
 *   <li><strong>No added latency, no failure propagation.</strong> Recording appends to
 *       a bounded in-memory queue drained by a background thread. A collector that is
 *       down, slow or absent changes nothing observable in the service, including its
 *       response times.</li>
 *   <li><strong>Nothing on the response path.</strong> No response header, no extra
 *       route, no marker in an error body, no log line on the happy path. Clients,
 *       caches and captures of this service look the same whether the agent is loaded
 *       or not.</li>
 *   <li><strong>The peer is what the socket said.</strong> Every record carries the
 *       address the filter observed on the connection, and that is the only address
 *       anything downstream classifies traffic on. Forwarded headers travel as
 *       description; an address a caller announced about itself never becomes the
 *       peer.</li>
 * </ol>
 *
 * <p>And one the dashboards depend on: route <em>templates</em>, never URLs.
 * {@code /api/orders/{id}} is one series; the concrete path rides along on the record.
 *
 * <p>Wiring a Spring MVC service: nothing. The auto-configuration installs the filter,
 * the route interceptor and the asynchronous decorator. Wiring anything else: register
 * {@code internal.telemetry.servlet.TelemetryFilter} first in the chain.
 *
 * <p>Raising a signal, from anywhere in the call stack, however deep:
 * <pre>{@code
 * Telemetry.signal("console.orgs.invoices.foreign_scope_served",
 *                  SignalOptions.payload(requestedAccount).withDetail("rows outside caller scope"));
 * }</pre>
 *
 * <p>Registering an outbound call whose destination came from a request, immediately
 * before making it:
 * <pre>{@code
 * Telemetry.outbound(url, "console.integrations.probe.offlist_host_fetched", "endpoint");
 * }</pre>
 */
package internal.telemetry;
