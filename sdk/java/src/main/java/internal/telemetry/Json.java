package internal.telemetry;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * The smallest JSON reader/writer that does this library's job.
 *
 * <p>Written here rather than taken from a library because this artifact is dropped
 * into services whose dependency trees are already contentious: adding a JSON provider
 * would force a version on every host, and a version conflict in an observability
 * agent is an outage in the service it observes. The shapes handled are the ones the
 * collector speaks and the ones request bodies arrive in, and nothing else.
 *
 * <p>Both directions are total. {@link #write} coerces whatever it is handed;
 * {@link #parse} returns {@code null} for input it cannot read instead of raising, so a
 * malformed body costs one attribute rather than a served request.
 */
public final class Json {

    private Json() {
    }

    // ------------------------------------------------------------------ writing

    /** Serialise a map, list, string, number, boolean or null. */
    public static String write(Object value) {
        StringBuilder out = new StringBuilder(256);
        writeValue(out, value, 0);
        return out.toString();
    }

    private static void writeValue(StringBuilder out, Object value, int depth) {
        if (depth > 64 || value == null) {
            out.append("null");
            return;
        }
        switch (value) {
            case String s -> writeString(out, s);
            case Boolean b -> out.append(b.booleanValue() ? "true" : "false");
            case Double d -> writeDouble(out, d.doubleValue());
            case Float f -> writeDouble(out, f.doubleValue());
            case Number n -> out.append(n.toString());
            case Map<?, ?> map -> {
                out.append('{');
                boolean first = true;
                for (Map.Entry<?, ?> entry : map.entrySet()) {
                    if (entry.getValue() == null) {
                        // Absent and null mean the same thing to the collector, and a
                        // record that omits them is smaller on a queue that is bounded.
                        continue;
                    }
                    if (!first) {
                        out.append(',');
                    }
                    first = false;
                    writeString(out, String.valueOf(entry.getKey()));
                    out.append(':');
                    writeValue(out, entry.getValue(), depth + 1);
                }
                out.append('}');
            }
            case Iterable<?> items -> {
                out.append('[');
                boolean first = true;
                for (Object item : items) {
                    if (!first) {
                        out.append(',');
                    }
                    first = false;
                    writeValue(out, item, depth + 1);
                }
                out.append(']');
            }
            case Object[] items -> writeValue(out, List.of(items), depth);
            default -> writeString(out, String.valueOf(value));
        }
    }

    private static void writeDouble(StringBuilder out, double d) {
        // JSON has no encoding for these; a record carrying one would be dropped whole
        // by the receiver, so the value alone is sacrificed.
        out.append(Double.isFinite(d) ? Double.toString(d) : "null");
    }

    private static void writeString(StringBuilder out, String s) {
        out.append('"');
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '"' -> out.append("\\\"");
                case '\\' -> out.append("\\\\");
                case '\n' -> out.append("\\n");
                case '\r' -> out.append("\\r");
                case '\t' -> out.append("\\t");
                case '\b' -> out.append("\\b");
                case '\f' -> out.append("\\f");
                default -> {
                    // Lone surrogates survive percent-decoding and are legal in a Java
                    // String but not in JSON text; escaping every one of them keeps a
                    // strict receiver from rejecting the whole batch.
                    if (c < 0x20 || c == 0x7f || Character.isSurrogate(c)) {
                        out.append(String.format("\\u%04x", (int) c));
                    } else {
                        out.append(c);
                    }
                }
            }
        }
        out.append('"');
    }

    // ------------------------------------------------------------------ reading

    /**
     * Parse a document. Returns a {@link LinkedHashMap} (insertion-ordered, so a
     * flattened body lists its fields the way the client sent them), a {@link List},
     * a {@link String}, a {@link Long} or {@link Double}, a {@link Boolean}, or
     * {@code null} — both for a JSON {@code null} and for anything unreadable.
     */
    public static Object parse(String text) {
        if (text == null) {
            return null;
        }
        try {
            Reader reader = new Reader(text);
            reader.skipWhitespace();
            Object value = reader.readValue(0);
            reader.skipWhitespace();
            return reader.atEnd() ? value : null;
        } catch (RuntimeException unreadable) {
            return null;
        }
    }

    private static final class Reader {
        private final String src;
        private int pos;

        Reader(String src) {
            this.src = src;
        }

        boolean atEnd() {
            return pos >= src.length();
        }

        void skipWhitespace() {
            while (pos < src.length()) {
                char c = src.charAt(pos);
                if (c == ' ' || c == '\t' || c == '\n' || c == '\r') {
                    pos++;
                } else {
                    return;
                }
            }
        }

        Object readValue(int depth) {
            if (depth > 128) {
                throw new IllegalStateException("nesting");
            }
            skipWhitespace();
            char c = peek();
            return switch (c) {
                case '{' -> readObject(depth);
                case '[' -> readArray(depth);
                case '"' -> readString();
                case 't' -> readLiteral("true", Boolean.TRUE);
                case 'f' -> readLiteral("false", Boolean.FALSE);
                case 'n' -> readLiteral("null", null);
                default -> readNumber();
            };
        }

        private Map<String, Object> readObject(int depth) {
            expect('{');
            Map<String, Object> out = new LinkedHashMap<>();
            skipWhitespace();
            if (peek() == '}') {
                pos++;
                return out;
            }
            while (true) {
                skipWhitespace();
                String key = readString();
                skipWhitespace();
                expect(':');
                out.put(key, readValue(depth + 1));
                skipWhitespace();
                char c = next();
                if (c == '}') {
                    return out;
                }
                if (c != ',') {
                    throw new IllegalStateException("object");
                }
            }
        }

        private List<Object> readArray(int depth) {
            expect('[');
            List<Object> out = new ArrayList<>();
            skipWhitespace();
            if (peek() == ']') {
                pos++;
                return out;
            }
            while (true) {
                out.add(readValue(depth + 1));
                skipWhitespace();
                char c = next();
                if (c == ']') {
                    return out;
                }
                if (c != ',') {
                    throw new IllegalStateException("array");
                }
            }
        }

        private String readString() {
            expect('"');
            StringBuilder out = new StringBuilder();
            while (true) {
                char c = next();
                if (c == '"') {
                    return out.toString();
                }
                if (c != '\\') {
                    out.append(c);
                    continue;
                }
                char esc = next();
                switch (esc) {
                    case '"' -> out.append('"');
                    case '\\' -> out.append('\\');
                    case '/' -> out.append('/');
                    case 'b' -> out.append('\b');
                    case 'f' -> out.append('\f');
                    case 'n' -> out.append('\n');
                    case 'r' -> out.append('\r');
                    case 't' -> out.append('\t');
                    case 'u' -> {
                        if (pos + 4 > src.length()) {
                            throw new IllegalStateException("escape");
                        }
                        out.append((char) Integer.parseInt(src.substring(pos, pos + 4), 16));
                        pos += 4;
                    }
                    default -> throw new IllegalStateException("escape");
                }
            }
        }

        private Object readLiteral(String word, Object value) {
            if (!src.startsWith(word, pos)) {
                throw new IllegalStateException("literal");
            }
            pos += word.length();
            return value;
        }

        private Object readNumber() {
            int start = pos;
            while (pos < src.length() && "+-0123456789.eE".indexOf(src.charAt(pos)) >= 0) {
                pos++;
            }
            String raw = src.substring(start, pos);
            if (raw.isEmpty()) {
                throw new IllegalStateException("number");
            }
            if (raw.indexOf('.') < 0 && raw.indexOf('e') < 0 && raw.indexOf('E') < 0) {
                try {
                    return Long.valueOf(raw);
                } catch (NumberFormatException tooWide) {
                    // Beyond long: keep the magnitude rather than losing the field.
                    return Double.valueOf(raw);
                }
            }
            return Double.valueOf(raw);
        }

        private char peek() {
            if (pos >= src.length()) {
                throw new IllegalStateException("end");
            }
            return src.charAt(pos);
        }

        private char next() {
            char c = peek();
            pos++;
            return c;
        }

        private void expect(char c) {
            if (next() != c) {
                throw new IllegalStateException("expected " + c);
            }
        }
    }
}
