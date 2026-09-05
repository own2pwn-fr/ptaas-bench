package internal.telemetry;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.function.BiConsumer;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Turning a raw request into described inputs.
 *
 * <p>Everything is parsed from the bytes the client sent rather than from the
 * container's parsed view. Two reasons, both learned the hard way. The container only
 * exposes what the handler asked for, and the requests worth looking at are usually the
 * ones where the handler asked for the wrong thing. And on the Servlet API,
 * {@code getParameter*} merges the query string with a form body and consumes the input
 * stream in the process, which would take the body away from the application.
 */
public final class Attributes {

    /** Collector limit on a sample. */
    public static final int SAMPLE_MAX_CHARS = 256;

    /** Depth guard for a nested body. Documents a few megabytes deep do arrive. */
    public static final int JSON_DEPTH_MAX = 16;

    private Attributes() {
    }

    // ------------------------------------------------------------------ describing

    public static String sha256(byte[] raw) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            return HexFormat.of().formatHex(digest.digest(raw));
        } catch (NoSuchAlgorithmException impossible) {
            // Every conformant platform ships SHA-256; if this one does not, an empty
            // digest costs one dashboard row and nothing else.
            return "";
        }
    }

    public static String sha256(String raw) {
        return sha256(raw.getBytes(StandardCharsets.UTF_8));
    }

    /** Truncate for display without leaving half a surrogate pair behind. */
    public static String sample(String raw) {
        if (raw.length() <= SAMPLE_MAX_CHARS) {
            return raw;
        }
        String cut = raw.substring(0, SAMPLE_MAX_CHARS);
        char last = cut.charAt(cut.length() - 1);
        return Character.isHighSurrogate(last) ? cut.substring(0, cut.length() - 1) : cut;
    }

    /** Describe one input given its textual value. */
    public static Attribute describe(String name, String in, String value) {
        String text = value == null ? "" : value;
        byte[] raw = text.getBytes(StandardCharsets.UTF_8);
        return new Attribute(name, in, sha256(raw), raw.length, sample(text));
    }

    /** Describe one input given raw bytes, e.g. an unparsed body or a multipart part. */
    public static Attribute describe(String name, String in, byte[] value) {
        byte[] raw = value == null ? new byte[0] : value;
        return new Attribute(name, in, sha256(raw), raw.length,
                sample(new String(raw, StandardCharsets.UTF_8)));
    }

    // ------------------------------------------------------------------ json bodies

    /**
     * Render a JSON leaf the way it looked on the wire.
     *
     * <p>{@code "laptop"} has to hash to the digest of {@code laptop}, and a number has
     * to hash like its textual form, so that the same value carried as JSON, as a form
     * field or as a query parameter groups together downstream. Hence: strings raw,
     * everything else in its JSON spelling.
     */
    public static String scalarText(Object value) {
        if (value instanceof String s) {
            return s;
        }
        if (value == null) {
            return "null";
        }
        if (value instanceof Boolean b) {
            return b.booleanValue() ? "true" : "false";
        }
        return Json.write(value);
    }

    /**
     * Walk a decoded document, handing every leaf to {@code sink} under its dotted path.
     *
     * <p>{@code {"filter":{"tags":["a"]}}} yields {@code filter.tags.0}. Empty
     * containers are emitted as leaves so that "the client sent this key" stays visible
     * even when it sent nothing inside it. A prefix of {@code ""} at the root becomes
     * {@code body}, so a bare scalar body is still addressable by name.
     */
    public static void flattenJson(Object value, String prefix, BiConsumer<String, String> sink) {
        flattenJson(value, prefix, sink, 0);
    }

    private static void flattenJson(Object value, String prefix, BiConsumer<String, String> sink, int depth) {
        if (depth > JSON_DEPTH_MAX) {
            return;
        }
        String name = prefix.isEmpty() ? "body" : prefix;
        if (value instanceof Map<?, ?> map) {
            if (map.isEmpty()) {
                sink.accept(name, "{}");
                return;
            }
            for (Map.Entry<?, ?> entry : map.entrySet()) {
                String key = String.valueOf(entry.getKey());
                String path = prefix.isEmpty() ? key : prefix + "." + key;
                flattenJson(entry.getValue(), path, sink, depth + 1);
            }
            return;
        }
        if (value instanceof List<?> items) {
            if (items.isEmpty()) {
                sink.accept(name, "[]");
                return;
            }
            for (int i = 0; i < items.size(); i++) {
                String path = prefix.isEmpty() ? Integer.toString(i) : prefix + "." + i;
                flattenJson(items.get(i), path, sink, depth + 1);
            }
            return;
        }
        sink.accept(name, scalarText(value));
    }

    // ------------------------------------------------------------------ wire formats

    /**
     * Split a query string or a urlencoded body into pairs, keeping blank values.
     *
     * <p>Percent-decoding is done by hand rather than with {@code URLDecoder} so that a
     * broken escape keeps its raw bytes instead of raising: a malformed parameter is
     * usually the reason someone is reading the record in the first place.
     */
    public static List<String[]> parsePairs(String encoded) {
        List<String[]> out = new ArrayList<>();
        if (encoded == null || encoded.isEmpty()) {
            return out;
        }
        for (String chunk : encoded.split("&")) {
            if (chunk.isEmpty()) {
                continue;
            }
            int eq = chunk.indexOf('=');
            String name = eq < 0 ? chunk : chunk.substring(0, eq);
            String value = eq < 0 ? "" : chunk.substring(eq + 1);
            out.add(new String[]{formDecode(name), formDecode(value)});
        }
        return out;
    }

    private static String formDecode(String raw) {
        if (raw.indexOf('%') < 0 && raw.indexOf('+') < 0) {
            return raw;
        }
        byte[] bytes = raw.getBytes(StandardCharsets.ISO_8859_1);
        byte[] out = new byte[bytes.length];
        int len = 0;
        for (int i = 0; i < bytes.length; i++) {
            byte b = bytes[i];
            if (b == '+') {
                out[len++] = ' ';
            } else if (b == '%' && i + 2 < bytes.length) {
                int hi = Character.digit(bytes[i + 1], 16);
                int lo = Character.digit(bytes[i + 2], 16);
                if (hi >= 0 && lo >= 0) {
                    out[len++] = (byte) ((hi << 4) | lo);
                    i += 2;
                } else {
                    out[len++] = b;
                }
            } else {
                out[len++] = b;
            }
        }
        return new String(out, 0, len, StandardCharsets.UTF_8);
    }

    /**
     * Split a Cookie header by hand.
     *
     * <p>The container's cookie parser discards pairs it considers illegal, and a
     * malformed cookie is exactly the one worth seeing.
     */
    public static List<String[]> parseCookieHeader(String header) {
        List<String[]> out = new ArrayList<>();
        if (header == null || header.isEmpty()) {
            return out;
        }
        for (String chunk : header.split(";")) {
            String part = chunk.strip();
            if (part.isEmpty()) {
                continue;
            }
            int eq = part.indexOf('=');
            String name = (eq < 0 ? part : part.substring(0, eq)).strip();
            if (name.isEmpty()) {
                continue;
            }
            String value = eq < 0 ? "" : part.substring(eq + 1).strip();
            if (value.length() >= 2 && value.startsWith("\"") && value.endsWith("\"")) {
                value = value.substring(1, value.length() - 1);
            }
            out.add(new String[]{name, value});
        }
        return out;
    }

    private static final Pattern DISPOSITION_NAME =
            Pattern.compile("name=\"((?:[^\"\\\\]|\\\\.)*)\"", Pattern.CASE_INSENSITIVE);
    private static final Pattern DISPOSITION_FILENAME =
            Pattern.compile("filename=\"((?:[^\"\\\\]|\\\\.)*)\"", Pattern.CASE_INSENSITIVE);
    private static final Pattern BOUNDARY =
            Pattern.compile("boundary=\"?([^\";,]+)\"?", Pattern.CASE_INSENSITIVE);

    /**
     * Walk a multipart body, handing {@code (fieldName, value)} to {@code sink}, plus
     * {@code (fieldName + ".filename", clientName)} for file parts.
     *
     * <p>Hand-rolled rather than delegated to the container's part parser for three
     * reasons: the container's parser needs a multipart configuration the host may not
     * have, it consumes the stream the application still needs, and it raises on the
     * malformed bodies (missing terminator, truncated upload, bogus part headers) that
     * are precisely the interesting ones. Nothing here raises.
     *
     * <p>File bytes are not described. They can be arbitrarily large and nothing
     * addresses them by value; the client-supplied name is the interesting half.
     */
    public static void walkMultipart(byte[] body, String contentType, BiConsumer<String, byte[]> sink) {
        if (body == null || body.length == 0 || contentType == null) {
            return;
        }
        Matcher m = BOUNDARY.matcher(contentType);
        if (!m.find()) {
            return;
        }
        byte[] delimiter = ("--" + m.group(1)).getBytes(StandardCharsets.ISO_8859_1);
        int from = 0;
        while (from < body.length) {
            int start = indexOf(body, delimiter, from);
            if (start < 0) {
                return;
            }
            int next = indexOf(body, delimiter, start + delimiter.length);
            int end = next < 0 ? body.length : next;
            emitPart(body, start + delimiter.length, end, sink);
            if (next < 0) {
                return;
            }
            from = next;
        }
    }

    private static void emitPart(byte[] body, int start, int end, BiConsumer<String, byte[]> sink) {
        byte[] separator = {'\r', '\n', '\r', '\n'};
        int headEnd = indexOf(body, separator, start);
        if (headEnd < 0 || headEnd >= end) {
            return;
        }
        String head = new String(body, start, headEnd - start, StandardCharsets.ISO_8859_1);
        Matcher name = DISPOSITION_NAME.matcher(head);
        if (!name.find()) {
            return;
        }
        int valueStart = headEnd + separator.length;
        int valueEnd = end;
        // Trim the CRLF that belongs to the delimiter rather than to the value.
        while (valueEnd > valueStart && (body[valueEnd - 1] == '\n' || body[valueEnd - 1] == '\r')) {
            valueEnd--;
        }
        String field = name.group(1);
        Matcher file = DISPOSITION_FILENAME.matcher(head);
        if (file.find()) {
            sink.accept(field + ".filename", file.group(1).getBytes(StandardCharsets.UTF_8));
            sink.accept(field, new byte[0]);
            return;
        }
        byte[] value = new byte[Math.max(0, valueEnd - valueStart)];
        System.arraycopy(body, valueStart, value, 0, value.length);
        sink.accept(field, value);
    }

    private static int indexOf(byte[] haystack, byte[] needle, int from) {
        outer:
        for (int i = Math.max(0, from); i + needle.length <= haystack.length; i++) {
            for (int j = 0; j < needle.length; j++) {
                if (haystack[i + j] != needle[j]) {
                    continue outer;
                }
            }
            return i;
        }
        return -1;
    }

    // ------------------------------------------------------------------ headers

    /**
     * Headers worth describing: the ones a handler, a proxy or a cache may key
     * behaviour off. Everything {@code x-*} is included as well, because custom headers
     * are where per-account routing and feature selection live.
     */
    private static final List<String> DESCRIBED_HEADERS = List.of(
            "host", "referer", "referrer", "user-agent", "origin", "content-type",
            "accept-language", "authorization", "forwarded", "true-client-ip");

    public static boolean isDescribedHeader(String name) {
        String lowered = name.toLowerCase(Locale.ROOT);
        return DESCRIBED_HEADERS.contains(lowered) || lowered.startsWith("x-");
    }

    /** Base media type, lower-cased, without parameters. */
    public static String baseContentType(String contentType) {
        if (contentType == null) {
            return "";
        }
        int semi = contentType.indexOf(';');
        return (semi < 0 ? contentType : contentType.substring(0, semi)).strip().toLowerCase(Locale.ROOT);
    }
}
