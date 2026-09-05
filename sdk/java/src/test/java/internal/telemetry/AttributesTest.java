package internal.telemetry;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.nio.charset.StandardCharsets;
import java.util.List;
import org.junit.jupiter.api.Test;

class AttributesTest {

    private static List<String> names(AttributeCollector collector) {
        return collector.entries().stream().map(a -> a.in() + ":" + a.name()).toList();
    }

    @Test
    void digestMatchesTheBytesOnTheWire() {
        // Must agree with what every other agent in the estate computes for the same
        // value, or the same request recorded at two tiers looks like two requests.
        assertEquals(Attributes.sha256("laptop".getBytes(StandardCharsets.UTF_8)),
                Attributes.describe("q", "query", "laptop").valueSha256());
        assertEquals(6, Attributes.describe("q", "query", "laptop").valueLen());
    }

    @Test
    void sampleIsClippedWithoutSplittingASurrogatePair() {
        String emoji = "🚀";
        String value = "x".repeat(Attributes.SAMPLE_MAX_CHARS - 1) + emoji;
        String sample = Attributes.describe("q", "query", value).sample();
        assertEquals(Attributes.SAMPLE_MAX_CHARS - 1, sample.length());
        assertFalse(Character.isHighSurrogate(sample.charAt(sample.length() - 1)));
    }

    @Test
    void repeatedNameWithADifferentValueIsKept() {
        // The property this whole class exists for. A collector keyed on (location,
        // name) alone would report one `id` here, and a request that behaved
        // differently from a single-valued one would look identical on the record.
        AttributeCollector collector = new AttributeCollector(64);
        collector.addPairs("id=1&id=2&id=1", "query");
        assertEquals(2, collector.size());
        assertEquals(List.of("query:id", "query:id"), names(collector));
        assertEquals(List.of("1", "2"), collector.entries().stream().map(Attribute::sample).toList());
    }

    @Test
    void identicalRepeatsCollapse() {
        AttributeCollector collector = new AttributeCollector(64);
        collector.addPairs("page=2&page=2", "query");
        assertEquals(1, collector.size());
    }

    @Test
    void jsonBodyIsFlattenedByDottedPath() {
        AttributeCollector collector = new AttributeCollector(64);
        String body = "{\"filter\":{\"tags\":[\"a\",\"b\"],\"empty\":{}},\"rows\":40000000,\"on\":true}";
        collector.addBody(body.getBytes(StandardCharsets.UTF_8), "application/json");
        assertEquals(List.of("json:filter.tags.0", "json:filter.tags.1", "json:filter.empty",
                "json:rows", "json:on"), names(collector));
        assertEquals("40000000", collector.entries().get(3).sample());
        assertEquals("true", collector.entries().get(4).sample());
    }

    @Test
    void aBodyThatClaimsJsonAndIsNotIsKeptWhole() {
        AttributeCollector collector = new AttributeCollector(64);
        collector.addBody("{not json".getBytes(StandardCharsets.UTF_8), "application/json");
        assertEquals(List.of("raw:body"), names(collector));
    }

    @Test
    void xmlBodyIsKeptWholeUnderRaw() {
        AttributeCollector collector = new AttributeCollector(64);
        collector.addBody("<a><b>c</b></a>".getBytes(StandardCharsets.UTF_8), "application/xml");
        assertEquals(List.of("raw:body"), names(collector));
    }

    @Test
    void multipartYieldsFieldNamesAndClientFileNames() {
        String boundary = "----x1";
        String body = "--" + boundary + "\r\n"
                + "Content-Disposition: form-data; name=\"note\"\r\n\r\nhello\r\n"
                + "--" + boundary + "\r\n"
                + "Content-Disposition: form-data; name=\"archive\"; filename=\"rates.zip\"\r\n"
                + "Content-Type: application/zip\r\n\r\nPKbinary\r\n"
                + "--" + boundary + "--\r\n";
        AttributeCollector collector = new AttributeCollector(64);
        collector.addBody(body.getBytes(StandardCharsets.ISO_8859_1),
                "multipart/form-data; boundary=" + boundary);
        List<String> observed = names(collector);
        assertTrue(observed.contains("multipart:note"), observed.toString());
        assertTrue(observed.contains("multipart:archive.filename"), observed.toString());
        assertEquals("rates.zip",
                collector.entries().stream()
                        .filter(a -> a.name().equals("archive.filename"))
                        .findFirst().orElseThrow().sample());
    }

    @Test
    void malformedMultipartDoesNotRaiseAndStillYieldsSomething() {
        AttributeCollector collector = new AttributeCollector(64);
        collector.addBody("--nope\r\ntruncated".getBytes(StandardCharsets.UTF_8),
                "multipart/form-data; boundary=nope");
        assertEquals(List.of("raw:body"), names(collector));
    }

    @Test
    void cookieHeaderIsSplitByHandIncludingMalformedPairs() {
        AttributeCollector collector = new AttributeCollector(64);
        collector.addCookieHeader("mrd_session=abc; mrd_layout=\"rO0AB\"; broken; consent=1");
        // A name with no value is still something the client sent, and a cookie that
        // fails to parse is usually why the request is being looked at, so it is kept.
        assertEquals(List.of("cookie:mrd_session", "cookie:mrd_layout", "cookie:broken",
                "cookie:consent"), names(collector));
        assertEquals("rO0AB", collector.entries().get(1).sample());
        assertEquals("", collector.entries().get(2).sample());
    }

    @Test
    void percentEscapesAreDecodedAndBrokenOnesSurvive() {
        AttributeCollector collector = new AttributeCollector(64);
        collector.addPairs("a=hello+world&b=%2Fetc%2Fpasswd&c=%zz", "query");
        assertEquals("hello world", collector.entries().get(0).sample());
        assertEquals("/etc/passwd", collector.entries().get(1).sample());
        assertEquals("%zz", collector.entries().get(2).sample());
    }

    @Test
    void collectorIsBoundedAndSaysSo() {
        AttributeCollector collector = new AttributeCollector(3);
        for (int i = 0; i < 50; i++) {
            collector.add("k" + i, "query", "v" + i);
        }
        assertEquals(3, collector.size());
        assertTrue(collector.isTruncated());
    }

    @Test
    void headerAllowlistCoversTheOnesThatChangeBehaviour() {
        assertTrue(Attributes.isDescribedHeader("host"));
        assertTrue(Attributes.isDescribedHeader("Authorization"));
        assertTrue(Attributes.isDescribedHeader("x-account-context"));
        assertFalse(Attributes.isDescribedHeader("accept-encoding"));
        assertFalse(Attributes.isDescribedHeader("sec-fetch-mode"));
    }
}
