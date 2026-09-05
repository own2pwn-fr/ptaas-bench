package internal.telemetry;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.stream.Stream;
import org.junit.jupiter.api.Test;

/**
 * This artifact ships inside the services it instruments, so anything a reader of that
 * filesystem could find has to look like an ordinary internal observability library and
 * nothing else.
 *
 * <p>The property rots back in silently: one debugging comment, one renamed symbol, one
 * copied paragraph, and it is gone. So it is a test rather than a review item.
 *
 * <p>Matching is on whole words and on stems, never on substrings. A naive
 * {@code contains()} fails on ordinary English — "available", "collaborator" and
 * "elaborate" all contain "lab" — and a check that cries wolf on every third comment
 * gets disabled within a week, which is the actual failure mode.
 */
class VocabularyTest {

    /** Terms that are revealing as whole words. */
    private static final List<String> TERMS = List.of(
            "benchmark", "ptaas", "vulnerable", "insecure", "deliberately",
            "ctf", "flag", "challenge", "lab", "testbed", "sandbox", "honeypot",
            "canary", "oracle", "ground truth", "trigger", "dvwa", "juice shop",
            "scanner", "evaluation", "scoring", "grader", "answer key", "pentest",
            "attacker", "adversary", "corpus", "planted", "instrumented target",
            "under test");

    /**
     * Terms whose inflections are equally revealing, checked as prefixes.
     *
     * <p>Over-strict on purpose: "scandinavian" starts with "scan" and trips this. A
     * false positive costs one rewritten comment; a false negative costs the cover of
     * every service this ships inside.
     */
    private static final List<String> STEMS =
            List.of("bench", "vuln", "exploit", "scan", "honeypot", "ctf", "dvwa", "ptaas");

    /**
     * Split into words on punctuation <em>and</em> on camel-case boundaries, so that an
     * identifier is matched the way a reader would read it.
     */
    static List<String> tokenize(String text) {
        List<String> out = new ArrayList<>();
        for (String word : text.replaceAll("([a-z0-9])([A-Z])", "$1 $2").split("[^A-Za-z0-9]+")) {
            if (!word.isEmpty()) {
                out.add(word.toLowerCase(Locale.ROOT));
            }
        }
        return out;
    }

    static Set<String> findForbidden(String text) {
        List<String> tokens = tokenize(text);
        String stream = " " + String.join(" ", tokens) + " ";
        Set<String> hits = new LinkedHashSet<>();
        for (String term : TERMS) {
            String phrase = String.join(" ", tokenize(term));
            if (!phrase.isEmpty() && stream.contains(" " + phrase + " ")) {
                hits.add(term);
            }
        }
        for (String stem : STEMS) {
            if (tokens.stream().anyMatch(token -> token.startsWith(stem))) {
                hits.add(stem + "*");
            }
        }
        return hits;
    }

    @Test
    void matcherFindsWholeWordsAndCamelCaseSegments() {
        assertTrue(findForbidden("private int benchMarkers = 1").contains("bench*"));
        assertTrue(findForbidden("the ground truth for this run").contains("ground truth"));
        assertTrue(findForbidden("vulnerabilities were found").contains("vuln*"));
    }

    @Test
    void matcherIgnoresOrdinaryEnglishThatMerelyContainsATerm() {
        // The reason this tokenises instead of using contains().
        assertEquals(Set.of(), findForbidden("no collaborator is available; elaborate labelling"));
        assertEquals(Set.of(), findForbidden("the interface is stable and configurable"));
        assertEquals(Set.of(), findForbidden("evaluated lazily, then re-evaluated"));
    }

    private static List<Path> shippedFiles() throws IOException {
        Path root = Path.of("").toAbsolutePath();
        List<Path> out = new ArrayList<>();
        for (String subtree : List.of("src/main", "pom.xml")) {
            Path start = root.resolve(subtree);
            if (!Files.exists(start)) {
                continue;
            }
            try (Stream<Path> walk = Files.walk(start)) {
                walk.filter(Files::isRegularFile).forEach(out::add);
            }
        }
        return out;
    }

    @Test
    void scansEnoughFilesThatABrokenPathCannotPassSilently() throws IOException {
        List<Path> files = shippedFiles();
        assertTrue(files.size() > 10, "expected the whole main source tree, found " + files.size());
        assertTrue(files.stream().anyMatch(p -> p.getFileName().toString().equals("pom.xml")));
    }

    @Test
    void noShippedFileCarriesRevealingVocabulary() throws IOException {
        Path root = Path.of("").toAbsolutePath();
        List<String> offenders = new ArrayList<>();
        for (Path file : shippedFiles()) {
            String relative = root.relativize(file).toString();
            Set<String> inName = findForbidden(relative);
            if (!inName.isEmpty()) {
                offenders.add(relative + " [name]: " + inName);
            }
            String body = new String(Files.readAllBytes(file), StandardCharsets.UTF_8);
            Set<String> inBody = findForbidden(body);
            if (!inBody.isEmpty()) {
                offenders.add(relative + ": " + inBody);
            }
        }
        assertEquals(List.of(), offenders);
    }

    @Test
    void noShippedFileCarriesAnIdentifierThatCouldBeMatchedAgainstThePlatform() throws IOException {
        for (Path file : shippedFiles()) {
            String body = new String(Files.readAllBytes(file), StandardCharsets.UTF_8);
            assertTrue(body.matches("(?s)^(?!.*\\bBENCH-[A-Z0-9]+-\\d{4}\\b).*$"),
                    file + " carries a catalog identifier");
            assertTrue(!body.toLowerCase(Locale.ROOT).contains("selftest"),
                    file + " names the platform's own probe");
        }
    }

    @Test
    void everyEnvironmentVariableReadIsPartOfTheAgentsOwnVocabulary() throws IOException {
        Set<String> names = new LinkedHashSet<>();
        java.util.regex.Pattern pattern =
                java.util.regex.Pattern.compile("\"(TELEMETRY_[A-Z0-9_]+)\"");
        for (Path file : shippedFiles()) {
            java.util.regex.Matcher matcher =
                    pattern.matcher(new String(Files.readAllBytes(file), StandardCharsets.UTF_8));
            while (matcher.find()) {
                names.add(matcher.group(1));
            }
        }
        assertEquals(Set.of(
                "TELEMETRY_SERVICE", "TELEMETRY_ENDPOINT", "TELEMETRY_ENABLED",
                "TELEMETRY_EVENTS_PATH", "TELEMETRY_CORRELATIONS_PATH",
                "TELEMETRY_SYNTHETIC_CIDRS", "TELEMETRY_QUEUE_MAX", "TELEMETRY_BATCH_MAX",
                "TELEMETRY_FLUSH_INTERVAL_MS", "TELEMETRY_TIMEOUT_MS",
                "TELEMETRY_MAX_BODY_BYTES", "TELEMETRY_MAX_PARAMS"), names);
        for (String name : names) {
            assertEquals(Set.of(), findForbidden(name));
        }
    }
}
