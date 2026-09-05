package internal.telemetry;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import internal.telemetry.servlet.PeerAddress;
import internal.telemetry.servlet.RouteTemplate;
import jakarta.servlet.http.HttpServletRequestWrapper;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.web.servlet.HandlerMapping;

class PeerAddressTest {

    @Test
    void theSocketPeerIsWhatIsReported() {
        MockHttpServletRequest request = new MockHttpServletRequest("GET", "/api/search");
        request.setRemoteAddr("10.88.0.31");
        assertEquals("10.88.0.31", PeerAddress.of(request));
    }

    @Test
    void aWrapperThatRewroteTheAddressIsSeenThrough() {
        // This is what a forwarded-header filter does: it wraps the request and answers
        // getRemoteAddr() from a header the caller sent. Unwrapping to the container's
        // own request makes the result correct whatever order filters ended up in.
        MockHttpServletRequest inner = new MockHttpServletRequest("GET", "/api/search");
        inner.setRemoteAddr("10.88.0.31");
        HttpServletRequestWrapper rewritten = new HttpServletRequestWrapper(inner) {
            @Override
            public String getRemoteAddr() {
                return "10.77.0.9";
            }
        };
        assertEquals("10.88.0.31", PeerAddress.of(rewritten));
    }

    @Test
    void anAddressTheCallerAlsoAnnouncedIsRefused() {
        // If the peer we were handed is one the caller named about itself, it is not
        // evidence, it is a claim arriving through a deployment we did not anticipate.
        // A record with no peer is honest; a record with the caller's own choice is not.
        MockHttpServletRequest request = new MockHttpServletRequest("GET", "/api/search");
        request.setRemoteAddr("10.77.0.9");
        request.addHeader("X-Forwarded-For", "10.77.0.9, 203.0.113.7");
        assertEquals("", PeerAddress.of(request));
    }

    @Test
    void theForwardedGrammarIsUnderstood() {
        MockHttpServletRequest request = new MockHttpServletRequest("GET", "/api/search");
        request.setRemoteAddr("192.0.2.5");
        request.addHeader("Forwarded", "for=\"192.0.2.5:4711\";proto=https");
        assertEquals("", PeerAddress.of(request));
    }

    @Test
    void anUnrelatedForwardedValueDoesNotDiscardTheRealPeer() {
        MockHttpServletRequest request = new MockHttpServletRequest("GET", "/api/search");
        request.setRemoteAddr("10.88.0.31");
        request.addHeader("X-Forwarded-For", "203.0.113.7");
        assertEquals("10.88.0.31", PeerAddress.of(request));
        assertEquals("203.0.113.7", PeerAddress.clientIp(request), "described, never trusted");
    }

    @Test
    void theAttributeNamesMatchTheFrameworkConstants() {
        // Addressed by name so a service that is not built on this framework still
        // links; compared here so a rename upstream fails a build rather than quietly
        // emptying every dashboard that groups by endpoint.
        assertEquals(HandlerMapping.BEST_MATCHING_PATTERN_ATTRIBUTE, RouteTemplate.BEST_MATCHING_PATTERN);
        assertEquals(HandlerMapping.PATH_WITHIN_HANDLER_MAPPING_ATTRIBUTE,
                RouteTemplate.PATH_WITHIN_HANDLER_MAPPING);
        assertEquals(HandlerMapping.URI_TEMPLATE_VARIABLES_ATTRIBUTE, RouteTemplate.URI_TEMPLATE_VARIABLES);
        assertTrue(RouteTemplate.UNMATCHED.equals("<unmatched>"));
    }
}
