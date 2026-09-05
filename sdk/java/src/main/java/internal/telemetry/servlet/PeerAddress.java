package internal.telemetry.servlet;

import internal.telemetry.SourceMatcher;
import jakarta.servlet.ServletRequest;
import jakarta.servlet.ServletRequestWrapper;
import jakarta.servlet.http.HttpServletRequest;
import java.util.List;
import java.util.Locale;

/**
 * Resolving the address of the peer that actually opened the connection.
 *
 * <p>This looks like a triviality and is not. The address is the only thing that
 * decides whether a record is counted as organic traffic or as the platform's own, and
 * a caller must never be able to make that decision for itself. Three things get in the
 * way, and all three are handled here.
 *
 * <ol>
 *   <li><strong>{@code getRemoteAddr()} is not always the socket.</strong> A forwarded-header
 *       filter rewrites it, in place, from a header the client sent, and does so by
 *       wrapping the request. So the request is unwrapped to the container's own object
 *       before the address is read: whatever a wrapper decided to say, the innermost
 *       request still reports what the socket reported.</li>
 *   <li><strong>Ordering is not a guarantee.</strong> This library asks to be installed
 *       ahead of any header-rewriting filter, but a host can order filters however it
 *       likes, and an ordering mistake is invisible. The unwrapping above makes the
 *       result correct either way.</li>
 *   <li><strong>Belt and braces.</strong> If the address that survives all that is one
 *       the caller <em>also</em> announced in a forwarded header, it is not evidence:
 *       it is the caller's claim, arriving through a deployment we did not anticipate.
 *       It is refused, and the record carries no peer at all rather than a value the
 *       caller chose.</li>
 * </ol>
 *
 * <p>The forwarded value is still described as an ordinary request input, and still
 * travels as {@code client_ip}, because it is usually the address a human wants to see.
 * It simply never classifies anything.
 */
public final class PeerAddress {

    /** Headers through which a caller can announce an address about itself. */
    static final List<String> FORWARDED_HEADERS = List.of(
            "x-forwarded-for", "x-real-ip", "forwarded", "true-client-ip", "client-ip");

    private PeerAddress() {
    }

    /**
     * The socket peer, or an empty string when what we were handed is a caller's claim.
     */
    public static String of(HttpServletRequest request) {
        String candidate = fromRootRequest(request);
        if (candidate == null || candidate.isEmpty()) {
            return "";
        }
        String normalised = SourceMatcher.normalise(candidate);
        if (normalised == null) {
            return "";
        }
        return announcedByCaller(request, normalised) ? "" : normalised;
    }

    /** The forwarded value if the caller sent one, else the peer. Description only. */
    public static String clientIp(HttpServletRequest request) {
        for (String header : FORWARDED_HEADERS) {
            String raw = request.getHeader(header);
            if (raw != null && !raw.isBlank()) {
                String first = raw.split(",")[0].strip();
                if (!first.isEmpty()) {
                    return first;
                }
            }
        }
        String direct = request.getRemoteAddr();
        return direct == null ? "" : direct;
    }

    private static String fromRootRequest(HttpServletRequest request) {
        ServletRequest current = request;
        // Depth-bounded: a wrapper chain that loops would otherwise hang a request.
        for (int i = 0; i < 32 && current instanceof ServletRequestWrapper wrapper; i++) {
            ServletRequest inner = wrapper.getRequest();
            if (inner == null || inner == current) {
                break;
            }
            current = inner;
        }
        return current.getRemoteAddr();
    }

    /**
     * True when one of the forwarded headers on this request names {@code peer}.
     *
     * <p>Understands the three spellings that actually arrive: a bare address, a
     * comma-separated chain, and the {@code Forwarded: for=192.0.2.1;proto=https} form
     * with optional quoting and an optional port.
     */
    static boolean announcedByCaller(HttpServletRequest request, String peer) {
        if (peer == null || peer.isEmpty()) {
            return false;
        }
        for (String header : FORWARDED_HEADERS) {
            java.util.Enumeration<String> values = request.getHeaders(header);
            if (values == null) {
                continue;
            }
            while (values.hasMoreElements()) {
                String raw = values.nextElement();
                if (raw == null || raw.isBlank()) {
                    continue;
                }
                for (String chunk : raw.replace(';', ',').split(",")) {
                    String candidate = chunk.strip().replace("\"", "");
                    int equals = candidate.indexOf('=');
                    if (equals >= 0) {
                        candidate = candidate.substring(equals + 1).strip().replace("\"", "");
                    }
                    String normalised = SourceMatcher.normalise(candidate);
                    if (normalised != null && normalised.equalsIgnoreCase(peer)) {
                        return true;
                    }
                    if (candidate.toLowerCase(Locale.ROOT).equals(peer.toLowerCase(Locale.ROOT))) {
                        return true;
                    }
                }
            }
        }
        return false;
    }
}
