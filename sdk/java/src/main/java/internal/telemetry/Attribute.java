package internal.telemetry;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * One input the handler could observe, described rather than stored.
 *
 * <p>Request values routinely carry personal data, credentials and card numbers, so
 * only a digest, a length and a short prefix leave the process. The digest is still
 * enough to tell an endpoint called with its documented default value from one called
 * with something else, which is what the input-drift dashboards are built on.
 *
 * @param name   the input's name, or its dotted path for a nested body field
 * @param in     where it arrived: query, body, json, path, header, cookie, multipart,
 *               raw, graphql or websocket
 * @param valueSha256 hex SHA-256 of the raw UTF-8 bytes
 * @param valueLen    length of those bytes
 * @param sample      the first {@value Attributes#SAMPLE_MAX_CHARS} characters
 */
public record Attribute(String name, String in, String valueSha256, int valueLen, String sample) {

    /** The wire shape: snake_case keys, because it is serialised straight to the collector. */
    public Map<String, Object> toMap() {
        Map<String, Object> out = new LinkedHashMap<>(6);
        out.put("name", name);
        out.put("in", in);
        out.put("value_sha256", valueSha256);
        out.put("value_len", valueLen);
        out.put("sample", sample);
        return out;
    }
}
