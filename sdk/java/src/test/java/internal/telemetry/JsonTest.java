package internal.telemetry;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;

class JsonTest {

    @Test
    void writesTheShapesTheCollectorSpeaks() {
        Map<String, Object> record = new LinkedHashMap<>();
        record.put("type", "signal");
        record.put("signal", "console.a.b");
        record.put("ts", 1.5d);
        record.put("synthetic", Boolean.TRUE);
        record.put("params", List.of(Map.of("name", "q")));
        assertEquals("{\"type\":\"signal\",\"signal\":\"console.a.b\",\"ts\":1.5,"
                + "\"synthetic\":true,\"params\":[{\"name\":\"q\"}]}", Json.write(record));
    }

    @Test
    void nullValuedKeysAreOmittedRatherThanWritten() {
        Map<String, Object> record = new LinkedHashMap<>();
        record.put("a", 1);
        record.put("b", null);
        assertEquals("{\"a\":1}", Json.write(record));
    }

    @Test
    void controlCharactersAndLoneSurrogatesAreEscaped() {
        // A lone surrogate survives percent-decoding and is legal in a Java string but
        // not in JSON text; a strict receiver would refuse the whole batch over one.
        String written = Json.write(Map.of("v", "a" + (char) 0x01 + "b" + (char) 0xD800 + "c"));
        assertTrue(written.contains("\\u0001"), written);
        assertTrue(written.contains("\\ud800"), written);
    }

    @Test
    void nonFiniteNumbersLoseThemselvesRatherThanTheRecord() {
        assertEquals("{\"v\":null}", Json.write(Map.of("v", Double.NaN)));
    }

    @Test
    void readsBackWhatItWrites() {
        String text = "{\"a\":[1,2.5,true,null,\"x\"],\"b\":{\"c\":\"d\"},\"e\":{}}";
        Object parsed = Json.parse(text);
        assertTrue(parsed instanceof Map);
        @SuppressWarnings("unchecked")
        Map<String, Object> map = (Map<String, Object>) parsed;
        assertEquals(List.of(1L, 2.5d, Boolean.TRUE, "x"),
                ((List<?>) map.get("a")).stream().filter(java.util.Objects::nonNull).toList());
        assertEquals(Map.of("c", "d"), map.get("b"));
        assertEquals(Map.of(), map.get("e"));
    }

    @Test
    void unreadableInputYieldsNullRatherThanRaising() {
        // Called on a request path with whatever a client sent; raising here would cost
        // a served request over a malformed body.
        assertNull(Json.parse("{unquoted:1}"));
        assertNull(Json.parse("{\"a\":1"));
        assertNull(Json.parse("[1,2]trailing"));
        assertNull(Json.parse(null));
    }

    @Test
    void deeplyNestedInputIsRefusedRatherThanOverflowingTheStack() {
        assertNull(Json.parse("[".repeat(500) + "]".repeat(500)));
    }

    @Test
    void insertionOrderIsPreservedSoFieldsListTheWayTheClientSentThem() {
        @SuppressWarnings("unchecked")
        Map<String, Object> parsed = (Map<String, Object>) Json.parse("{\"z\":1,\"a\":2,\"m\":3}");
        assertEquals(List.of("z", "a", "m"), List.copyOf(parsed.keySet()));
    }
}
