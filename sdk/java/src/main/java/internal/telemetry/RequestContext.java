package internal.telemetry;

import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;

/**
 * Ambient facts about the request currently being served.
 *
 * <p>Application code raises a counter with one call and no plumbing: it does not, and
 * should not, have to thread a request object down to whatever noticed something worth
 * counting. Those calls happen four frames deep inside a service class, behind a
 * transaction proxy, and sometimes on another thread entirely — so the facts a record
 * needs travel here instead of in an argument list.
 *
 * <p>Mutable, and shared by every thread the request fans out to. The two fields a
 * handler may still fill in ({@code route} and {@code authSubject}) are written once
 * from the request thread; {@link #extraAttributes()} is a copy-on-write list because a
 * helper on a pooled thread can contribute to it.
 */
public final class RequestContext {

    private final String requestId;
    private final String peerIp;
    private final String clientIp;
    private final boolean synthetic;
    private final List<Attribute> extraAttributes = new CopyOnWriteArrayList<>();
    private volatile String route;
    private volatile String authSubject;

    public RequestContext(String requestId, String peerIp, String clientIp, boolean synthetic) {
        this.requestId = requestId;
        this.peerIp = peerIp == null ? "" : peerIp;
        this.clientIp = clientIp == null ? "" : clientIp;
        this.synthetic = synthetic;
    }

    public String requestId() {
        return requestId;
    }

    /**
     * The address the socket reported, and only that.
     *
     * <p>Empty when what the container handed over turned out to be a caller's claim
     * rather than a socket address. See
     * {@link internal.telemetry.servlet.PeerAddress} for how that is decided and why it
     * is decided at all.
     */
    public String peerIp() {
        return peerIp;
    }

    /** Whatever the framework calls the client address, forwarded values included. Description only. */
    public String clientIp() {
        return clientIp;
    }

    /** True when the peer belongs to one of the configured generated-traffic networks. */
    public boolean synthetic() {
        return synthetic;
    }

    public String route() {
        return route;
    }

    public void route(String value) {
        this.route = value;
    }

    public String authSubject() {
        return authSubject;
    }

    public void authSubject(String value) {
        this.authSubject = value;
    }

    /**
     * Inputs contributed by helpers while the request runs.
     *
     * <p>Merged into the single request record the filter exports, so one request stays
     * one record however many helpers were called.
     */
    public List<Attribute> extraAttributes() {
        return extraAttributes;
    }
}
