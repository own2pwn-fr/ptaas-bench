package internal.telemetry;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.List;
import org.junit.jupiter.api.Test;

class SourceMatcherTest {

    @Test
    void matchesInsideAnIpv4Prefix() {
        SourceMatcher matcher = SourceMatcher.of(List.of("10.77.0.0/24"));
        assertTrue(matcher.matches("10.77.0.5"));
        assertFalse(matcher.matches("10.88.0.5"));
    }

    @Test
    void foldsTheDualStackFormBackToIpv4() {
        // A listener bound to both families reports an IPv4 peer this way. Comparing it
        // against an IPv4 prefix without folding silently never matches, which shows up
        // only in one deployment shape.
        SourceMatcher matcher = SourceMatcher.of(List.of("10.77.0.0/24"));
        assertTrue(matcher.matches("::ffff:10.77.0.5"));
    }

    @Test
    void understandsPortsBracketsAndZoneIndices() {
        assertEquals("10.77.0.5", SourceMatcher.normalise("10.77.0.5:54321"));
        assertEquals("fe80::1", SourceMatcher.normalise("[fe80::1]:443"));
        assertEquals("fe80::1", SourceMatcher.normalise("fe80::1%eth0"));
    }

    @Test
    void matchesInsideAnIpv6Prefix() {
        SourceMatcher matcher = SourceMatcher.of(List.of("fd00:dead::/32"));
        assertTrue(matcher.matches("fd00:dead:0:1::9"));
        assertFalse(matcher.matches("fd00:beef::1"));
    }

    @Test
    void bareAddressIsAHostRoute() {
        SourceMatcher matcher = SourceMatcher.of(List.of("10.77.0.5"));
        assertTrue(matcher.matches("10.77.0.5"));
        assertFalse(matcher.matches("10.77.0.6"));
    }

    @Test
    void aTypoInConfigurationIsSkippedRatherThanFatal() {
        SourceMatcher matcher = SourceMatcher.of(List.of("10.77.0.0/99", "not-an-address", "", "10.1.0.0/16"));
        assertEquals(1, matcher.size());
        assertTrue(matcher.matches("10.1.2.3"));
    }

    @Test
    void neverResolvesAName() {
        // A resolver call here would be a latency spike that appears for some inputs and
        // not others, on the request path, in the one component that must not have one.
        assertNull(SourceMatcher.literal("localhost"));
        assertNull(SourceMatcher.literal("collector.internal"));
        assertFalse(SourceMatcher.of(List.of("10.0.0.0/8")).matches("localhost"));
    }

    @Test
    void emptyMatcherMatchesNothing() {
        assertFalse(SourceMatcher.none().matches("10.77.0.5"));
    }
}
