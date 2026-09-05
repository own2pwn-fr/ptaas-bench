package internal.telemetry;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import internal.telemetry.servlet.RouteTemplate;
import internal.telemetry.servlet.TelemetryFilter;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CopyOnWriteArrayList;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockFilterChain;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;

class TelemetryFilterTest {

    private final List<Map<String, Object>> exported = new CopyOnWriteArrayList<>();
    private TelemetryClient client;

    @BeforeEach
    void setUp() {
        client = new TelemetryClient(TelemetryConfig.builder()
                .service("admin")
                .endpoint("http://otel-collector:8900")
                .enabled(true)
                .flushInterval(Duration.ofMillis(10))
                .syntheticCidrs(List.of("10.77.0.0/24"))
                .build());
        client.sender((path, batch) -> exported.addAll(batch));
    }

    @AfterEach
    void tearDown() {
        client.close(Duration.ofMillis(200));
    }

    private List<Map<String, Object>> drain() {
        client.flush(Duration.ofSeconds(2));
        return List.copyOf(exported);
    }

    private static MockHttpServletRequest routed(String method, String uri, String pattern) {
        MockHttpServletRequest request = new MockHttpServletRequest(method, uri);
        request.setRemoteAddr("10.88.0.31");
        if (pattern != null) {
            request.setAttribute(RouteTemplate.BEST_MATCHING_PATTERN, pattern);
            request.setAttribute(RouteTemplate.PATH_WITHIN_HANDLER_MAPPING, uri);
        }
        return request;
    }

    @SuppressWarnings("unchecked")
    private static List<Map<String, Object>> params(Map<String, Object> record) {
        return (List<Map<String, Object>>) record.get("params");
    }

    private static List<String> keys(Map<String, Object> record) {
        return params(record).stream().map(p -> p.get("in") + ":" + p.get("name")).toList();
    }

    @Test
    void oneRecordPerRequestCarryingTheRegisteredTemplate() throws Exception {
        MockHttpServletRequest request =
                routed("GET", "/api/orgs/1042/invoices", "/api/orgs/{orgId}/invoices");
        request.setAttribute(RouteTemplate.URI_TEMPLATE_VARIABLES, Map.of("orgId", "1042"));
        MockHttpServletResponse response = new MockHttpServletResponse();

        new TelemetryFilter(client).doFilter(request, response, new MockFilterChain());

        List<Map<String, Object>> records = drain();
        assertEquals(1, records.size());
        Map<String, Object> record = records.get(0);
        assertEquals("http_request", record.get("type"));
        assertEquals("admin", record.get("app"));
        assertEquals("GET", record.get("method"));
        // The template, not the URL — the whole point.
        assertEquals("/api/orgs/{orgId}/invoices", record.get("route"));
        assertEquals("/api/orgs/1042/invoices", record.get("path"));
        assertTrue(keys(record).contains("path:orgId"));
    }

    @Test
    void anUnmatchedRequestSaysSoAndStillCarriesItsPath() throws Exception {
        MockHttpServletRequest request = routed("GET", "/.git/config", null);
        MockHttpServletResponse response = new MockHttpServletResponse();
        response.setStatus(404);

        new TelemetryFilter(client).doFilter(request, response, new MockFilterChain());

        Map<String, Object> record = drain().get(0);
        assertEquals("<unmatched>", record.get("route"));
        assertEquals("/.git/config", record.get("path"));
        assertEquals(404, record.get("status"));
    }

    @Test
    void aDispatcherMountedUnderAPrefixKeepsThatPrefix() throws Exception {
        MockHttpServletRequest request = new MockHttpServletRequest("GET", "/console/api/search");
        request.setRemoteAddr("10.88.0.31");
        request.setAttribute(RouteTemplate.BEST_MATCHING_PATTERN, "/api/search");
        request.setAttribute(RouteTemplate.PATH_WITHIN_HANDLER_MAPPING, "/api/search");

        new TelemetryFilter(client).doFilter(request, new MockHttpServletResponse(), new MockFilterChain());

        assertEquals("/console/api/search", drain().get(0).get("route"));
    }

    @Test
    void everyInputLocationIsEnumerated() throws Exception {
        MockHttpServletRequest request = routed("POST", "/api/rules/preview", "/api/rules/preview");
        request.setQueryString("dryRun=1");
        request.setContentType("application/json");
        request.setContent("{\"expression\":\"weight > 1200\",\"sample\":{\"weightKg\":900}}"
                .getBytes(StandardCharsets.UTF_8));
        request.addHeader("Cookie", "mrd_session=abc; consent=1");
        request.addHeader("X-Account-Context", "1043");
        request.addHeader("User-Agent", "Mozilla/5.0");
        request.addHeader("Accept-Encoding", "gzip");

        // The chain reads the body the way a handler would.
        FilterChain chain = (req, res) -> ((HttpServletRequest) req).getInputStream().readAllBytes();
        new TelemetryFilter(client).doFilter(request, new MockHttpServletResponse(), chain);

        List<String> observed = keys(drain().get(0));
        assertTrue(observed.contains("query:dryRun"), observed.toString());
        assertTrue(observed.contains("json:expression"), observed.toString());
        assertTrue(observed.contains("json:sample.weightKg"), observed.toString());
        assertTrue(observed.contains("cookie:mrd_session"), observed.toString());
        assertTrue(observed.contains("cookie:consent"), observed.toString());
        assertTrue(observed.contains("header:x-account-context"), observed.toString());
        assertTrue(observed.contains("header:user-agent"), observed.toString());
        assertFalse(observed.contains("header:accept-encoding"), observed.toString());
    }

    @Test
    void aRepeatedParameterWithTwoValuesIsRecordedTwice() throws Exception {
        MockHttpServletRequest request = routed("GET", "/api/search", "/api/search");
        request.setQueryString("sort=name+asc&sort=updatedAt+desc");

        new TelemetryFilter(client).doFilter(request, new MockHttpServletResponse(), new MockFilterChain());

        List<Map<String, Object>> observed = params(drain().get(0)).stream()
                .filter(p -> "sort".equals(p.get("name"))).toList();
        assertEquals(2, observed.size(), "collapsing this would lose the technique entirely");
    }

    @Test
    void theBodyIsStillThereForTheApplication() throws Exception {
        MockHttpServletRequest request = routed("POST", "/api/notices", "/api/notices");
        request.setContentType("application/json");
        String body = "{\"title\":\"Rate card update\",\"body\":\"Sunday maintenance\"}";
        request.setContent(body.getBytes(StandardCharsets.UTF_8));

        List<String> seenByHandler = new ArrayList<>();
        FilterChain chain = (req, res) -> seenByHandler.add(
                new String(((HttpServletRequest) req).getInputStream().readAllBytes(),
                        StandardCharsets.UTF_8));

        new TelemetryFilter(client).doFilter(request, new MockHttpServletResponse(), chain);

        assertEquals(List.of(body), seenByHandler);
        assertTrue(keys(drain().get(0)).contains("json:title"));
    }

    @Test
    void aBodyTheHandlerNeverReadIsStillDescribed() throws Exception {
        // A route that refuses the request before looking at it is exactly the record
        // someone wants afterwards.
        MockHttpServletRequest request = routed("POST", "/api/intake/documents", "/api/intake/documents");
        request.setContentType("application/xml");
        request.setContent("<consignment><reference>CW-40118</reference></consignment>"
                .getBytes(StandardCharsets.UTF_8));
        MockHttpServletResponse response = new MockHttpServletResponse();
        response.setStatus(403);

        new TelemetryFilter(client).doFilter(request, response, new MockFilterChain());

        assertTrue(keys(drain().get(0)).contains("raw:body"));
    }

    @Test
    void formFieldsSurviveWhateverTheHandlerAsksFor() throws Exception {
        MockHttpServletRequest request = routed("POST", "/api/auth/login", "/api/auth/login");
        request.setContentType("application/x-www-form-urlencoded");
        request.setContent("email=h.lindqvist%40calderwood.example&password=atlas-pennant-5106"
                .getBytes(StandardCharsets.UTF_8));

        // The handler reads parameters rather than the stream, the way a form endpoint
        // usually does, so the bytes never pass through the wrapper. Nothing may be lost
        // either way. (The request double used here does not populate parameters from a
        // body the way a container does, which is precisely the path being covered: the
        // parsed view is empty, so the fields have to be recovered from the stream.)
        FilterChain chain = (req, res) -> req.getParameterMap();
        new TelemetryFilter(client).doFilter(request, new MockHttpServletResponse(), chain);

        List<String> observed = keys(drain().get(0));
        assertTrue(observed.contains("body:email"), observed.toString());
        assertTrue(observed.contains("body:password"), observed.toString());
    }

    @Test
    void theSocketPeerDecidesTheClassificationAndAHeaderNeverDoes() throws Exception {
        MockHttpServletRequest request = routed("GET", "/api/search", "/api/search");
        request.setRemoteAddr("10.88.0.31");
        // A caller announcing one of the estate's own addresses must not be able to
        // reclassify its own traffic, which is how it would erase itself from a report.
        request.addHeader("X-Forwarded-For", "10.77.0.9");

        new TelemetryFilter(client).doFilter(request, new MockHttpServletResponse(), new MockFilterChain());

        Map<String, Object> record = drain().get(0);
        assertEquals("10.88.0.31", record.get("peer_ip"));
        assertEquals(Boolean.FALSE, record.get("synthetic"));
        assertEquals("10.77.0.9", record.get("client_ip"), "still described, never trusted");
    }

    @Test
    void aPeerInsideTheEstateIsClassifiedAsGenerated() throws Exception {
        MockHttpServletRequest request = routed("GET", "/api/search", "/api/search");
        request.setRemoteAddr("10.77.0.9");

        new TelemetryFilter(client).doFilter(request, new MockHttpServletResponse(), new MockFilterChain());

        assertEquals(Boolean.TRUE, drain().get(0).get("synthetic"));
    }

    @Test
    void theResponseIsUntouched() throws Exception {
        MockHttpServletRequest request = routed("GET", "/api/search", "/api/search");
        MockHttpServletResponse response = new MockHttpServletResponse();

        new TelemetryFilter(client).doFilter(request, response, new MockFilterChain());

        // No header, no cookie, no body of our own: a capture of this service looks the
        // same whether the agent is loaded or not.
        assertEquals(Collections.emptyList(), new ArrayList<>(response.getHeaderNames()));
        assertEquals(0, response.getContentAsByteArray().length);
    }

    @Test
    void anInertClientRecordsNothingAndStillServesTheRequest() throws Exception {
        TelemetryClient inert = new TelemetryClient(TelemetryConfig.builder().service("").build());
        MockHttpServletRequest request = routed("GET", "/api/search", "/api/search");
        MockFilterChain chain = new MockFilterChain();

        new TelemetryFilter(inert).doFilter(request, new MockHttpServletResponse(), chain);

        assertNotNull(chain.getRequest());
        assertNull(request.getAttribute("internal.telemetry.context"));
    }
}
