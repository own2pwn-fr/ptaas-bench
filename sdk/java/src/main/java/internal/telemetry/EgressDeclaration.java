package internal.telemetry;

import java.net.URI;

/**
 * Describes an outbound call whose destination came from a request.
 *
 * <p>A request-controlled destination means the resulting egress — a name lookup, a
 * connection, a hit on some third party — appears in the network's own logs with
 * nothing tying it back to the request that caused it. Registering the pairing
 * beforehand is what lets the two sides be joined afterwards.
 */
public final class EgressDeclaration {

    private String signal;
    private String param;
    private String route;
    private String requestId;
    private String peerIp;
    private Boolean synthetic;

    public static EgressDeclaration from(String signal) {
        return new EgressDeclaration().withSignal(signal);
    }

    /** Dotted metric name identifying the code path making the call. */
    public EgressDeclaration withSignal(String value) {
        this.signal = value;
        return this;
    }

    /** Name of the input the destination came from. */
    public EgressDeclaration withParam(String value) {
        this.param = value;
        return this;
    }

    /** Route template of the request that caused the call; taken from context when omitted. */
    public EgressDeclaration withRoute(String value) {
        this.route = value;
        return this;
    }

    public EgressDeclaration withRequestId(String value) {
        this.requestId = value;
        return this;
    }

    /** Socket peer, for a caller not running inside an instrumented request. */
    public EgressDeclaration withPeerIp(String value) {
        this.peerIp = value;
        return this;
    }

    public EgressDeclaration withSynthetic(boolean value) {
        this.synthetic = value;
        return this;
    }

    String signal() {
        return signal;
    }

    String param() {
        return param;
    }

    String route() {
        return route;
    }

    String requestId() {
        return requestId;
    }

    String peerIp() {
        return peerIp;
    }

    Boolean synthetic() {
        return synthetic;
    }

    /**
     * Host part of a URL, or the value itself when it is already a host.
     *
     * <p>Hand-rolled rather than delegated to {@link URI}, because the values that reach
     * this method are frequently not well-formed URLs — that is often the point of
     * looking at them — and {@code URI} raises on those.
     */
    public static String hostOf(String destination) {
        String text = destination == null ? "" : destination.strip();
        if (text.isEmpty()) {
            return "";
        }
        int scheme = text.indexOf("//");
        if (scheme >= 0) {
            text = text.substring(scheme + 2);
        }
        int slash = text.indexOf('/');
        if (slash >= 0) {
            text = text.substring(0, slash);
        }
        int question = text.indexOf('?');
        if (question >= 0) {
            text = text.substring(0, question);
        }
        int at = text.lastIndexOf('@');
        if (at >= 0) {
            text = text.substring(at + 1);
        }
        if (text.startsWith("[")) {
            int close = text.indexOf(']');
            return close > 0 ? text.substring(1, close) : text.substring(1);
        }
        int colon = text.indexOf(':');
        if (colon >= 0) {
            text = text.substring(0, colon);
        }
        return text.toLowerCase(java.util.Locale.ROOT);
    }
}
