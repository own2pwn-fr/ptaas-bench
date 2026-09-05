package internal.telemetry;

import java.net.InetAddress;
import java.net.UnknownHostException;
import java.util.ArrayList;
import java.util.List;

/**
 * Membership test for a set of CIDR prefixes, used to classify a socket peer.
 *
 * <p>Two properties matter more than speed.
 *
 * <p>First, <strong>no name resolution, ever</strong>. Every candidate is checked
 * against a literal-address shape before it is handed to the platform, so a value that
 * turns out to be a hostname is rejected rather than looked up. A DNS round trip on the
 * request path would be a latency spike that appears only for certain inputs, which is
 * the worst possible shape for something a latency dashboard is built on.
 *
 * <p>Second, a malformed prefix in a deployment variable is skipped, not thrown. A typo
 * in configuration must never stop a service from starting.
 */
public final class SourceMatcher {

    private record Prefix(byte[] network, int bits) {
    }

    private final List<Prefix> prefixes;

    private SourceMatcher(List<Prefix> prefixes) {
        this.prefixes = prefixes;
    }

    /** A matcher that matches nothing. */
    public static SourceMatcher none() {
        return new SourceMatcher(List.of());
    }

    /**
     * Compile a list of prefixes. A bare address is taken as a host route
     * ({@code /32} or {@code /128}).
     */
    public static SourceMatcher of(List<String> entries) {
        List<Prefix> compiled = new ArrayList<>();
        for (String raw : entries) {
            String entry = raw == null ? "" : raw.strip();
            if (entry.isEmpty()) {
                continue;
            }
            int slash = entry.lastIndexOf('/');
            String addressPart = slash < 0 ? entry : entry.substring(0, slash);
            InetAddress address = literal(addressPart);
            if (address == null) {
                continue;
            }
            byte[] bytes = address.getAddress();
            int full = bytes.length * 8;
            int bits = full;
            if (slash >= 0) {
                try {
                    bits = Integer.parseInt(entry.substring(slash + 1).strip());
                } catch (NumberFormatException malformed) {
                    continue;
                }
            }
            if (bits < 0 || bits > full) {
                continue;
            }
            compiled.add(new Prefix(bytes, bits));
        }
        return new SourceMatcher(List.copyOf(compiled));
    }

    public int size() {
        return prefixes.size();
    }

    /** True when {@code candidate} is a literal address inside one of the prefixes. */
    public boolean matches(String candidate) {
        if (prefixes.isEmpty()) {
            return false;
        }
        InetAddress address = literal(normalise(candidate));
        if (address == null) {
            return false;
        }
        byte[] bytes = address.getAddress();
        for (Prefix prefix : prefixes) {
            if (prefix.network().length == bytes.length && contains(prefix, bytes)) {
                return true;
            }
        }
        return false;
    }

    private static boolean contains(Prefix prefix, byte[] address) {
        int fullBytes = prefix.bits() / 8;
        int remainder = prefix.bits() % 8;
        for (int i = 0; i < fullBytes; i++) {
            if (prefix.network()[i] != address[i]) {
                return false;
            }
        }
        if (remainder == 0) {
            return true;
        }
        int mask = 0xff << (8 - remainder);
        return (prefix.network()[fullBytes] & mask) == (address[fullBytes] & mask);
    }

    /**
     * Reduce a socket-reported address to a comparable literal.
     *
     * <p>Handles the four shapes that actually arrive: bracketed IPv6, a zone index
     * ({@code fe80::1%eth0}), a trailing port, and the IPv4-mapped form
     * ({@code ::ffff:10.0.0.4}) that a dual-stack listener reports for an IPv4 peer.
     * Comparing that last form against an IPv4 prefix silently never matches, which is
     * a bug that only shows up in one deployment shape and is invisible in every test
     * that binds to IPv4.
     */
    public static String normalise(String candidate) {
        if (candidate == null) {
            return null;
        }
        String value = candidate.strip();
        if (value.isEmpty()) {
            return null;
        }
        if (value.startsWith("[")) {
            int close = value.indexOf(']');
            value = close > 0 ? value.substring(1, close) : value.substring(1);
        } else if (value.indexOf(':') >= 0 && value.indexOf(':') == value.lastIndexOf(':')) {
            // Exactly one colon: an IPv4 address with a port, never an IPv6 address.
            value = value.substring(0, value.indexOf(':'));
        }
        int zone = value.indexOf('%');
        if (zone > 0) {
            value = value.substring(0, zone);
        }
        String lowered = value.toLowerCase(java.util.Locale.ROOT);
        if (lowered.startsWith("::ffff:") && lowered.indexOf('.') > 0) {
            value = value.substring(7);
        }
        return value;
    }

    /**
     * Parse an address, and only an address.
     *
     * <p>Returns {@code null} for anything that is not a literal, so nothing here can
     * ever reach a resolver.
     */
    public static InetAddress literal(String value) {
        if (value == null || value.isEmpty() || value.length() > 45) {
            return null;
        }
        boolean sawDigit = false;
        for (int i = 0; i < value.length(); i++) {
            char c = value.charAt(i);
            boolean ok = (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F')
                    || c == '.' || c == ':';
            if (!ok) {
                return null;
            }
            sawDigit |= c == '.' || c == ':' || (c >= '0' && c <= '9');
        }
        if (!sawDigit) {
            return null;
        }
        // A bare "abc" would be hex-only and could still be a host name; require the
        // structure of an address.
        if (value.indexOf('.') < 0 && value.indexOf(':') < 0) {
            return null;
        }
        try {
            return InetAddress.getByName(value);
        } catch (UnknownHostException notAnAddress) {
            return null;
        }
    }
}
