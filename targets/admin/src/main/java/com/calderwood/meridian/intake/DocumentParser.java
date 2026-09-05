package com.calderwood.meridian.intake;

import internal.telemetry.EgressDeclaration;
import internal.telemetry.SignalOptions;
import internal.telemetry.Telemetry;
import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.io.StringReader;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.Locale;
import java.util.concurrent.atomic.AtomicBoolean;
import javax.xml.parsers.DocumentBuilder;
import javax.xml.parsers.DocumentBuilderFactory;
import org.springframework.stereotype.Component;
import org.w3c.dom.Document;
import org.xml.sax.EntityResolver;
import org.xml.sax.InputSource;
import org.xml.sax.SAXException;

/**
 * Reads the XML that forwarders and terminal systems send us.
 *
 * <p>Thirty different forwarders send consignment documents and about half of them
 * include a doctype pointing at a schema on their own side, so the parser was made
 * permissive during onboarding and left that way. The resolver below is ours rather
 * than the platform's so that a document referring to something we cannot reach fails
 * as an empty value instead of failing the whole submission, which is what onboarding
 * actually needed.
 */
@Component
public class DocumentParser {

    private static final Duration FETCH_TIMEOUT = Duration.ofSeconds(4);

    /** What one parse observed, beyond the document itself. */
    public record Parsed(Document document, int resolvedReferences) {
    }

    /**
     * Parse a submitted document.
     *
     * @param body    the bytes as they arrived
     * @param counter the counter to raise when a reference outside the document was
     *                actually resolved and substituted
     * @param param   the input the document arrived in, recorded alongside
     * @param networkOnly raise the counter only for references that left the host
     */
    public Parsed parse(byte[] body, String counter, String param, boolean networkOnly)
            throws SAXException, IOException {
        AtomicBoolean raised = new AtomicBoolean();
        int[] resolved = {0};

        DocumentBuilderFactory factory = DocumentBuilderFactory.newInstance();
        factory.setNamespaceAware(false);
        factory.setExpandEntityReferences(true);
        DocumentBuilder builder;
        try {
            builder = factory.newDocumentBuilder();
        } catch (Exception unavailable) {
            throw new IOException("no XML parser available", unavailable);
        }
        builder.setEntityResolver(new ExternalReferenceResolver(counter, param, networkOnly,
                raised, resolved));
        builder.setErrorHandler(new QuietErrorHandler());
        Document document = builder.parse(new ByteArrayInputStream(body));
        return new Parsed(document, resolved[0]);
    }

    /** Fetches what a document points at, and says so. */
    private static final class ExternalReferenceResolver implements EntityResolver {

        private final String counter;
        private final String param;
        private final boolean networkOnly;
        private final AtomicBoolean raised;
        private final int[] resolved;

        ExternalReferenceResolver(String counter, String param, boolean networkOnly,
                                  AtomicBoolean raised, int[] resolved) {
            this.counter = counter;
            this.param = param;
            this.networkOnly = networkOnly;
            this.raised = raised;
            this.resolved = resolved;
        }

        @Override
        public InputSource resolveEntity(String publicId, String systemId) {
            if (systemId == null || systemId.isBlank()) {
                return null;
            }
            String scheme = schemeOf(systemId);
            String content;
            boolean overNetwork = "http".equals(scheme) || "https".equals(scheme)
                    || "ftp".equals(scheme);
            if (overNetwork) {
                // The lookup that follows is caused by this request; declaring it first
                // is what lets the two be joined in the network's own records.
                Telemetry.outbound(systemId,
                        EgressDeclaration.from(counter).withParam(param));
                content = fetch(systemId);
            } else {
                content = read(systemId);
            }
            if (content == null) {
                // Unreachable: the document keeps an empty value for that reference and
                // the rest of the submission is still accepted.
                return new InputSource(new StringReader(""));
            }
            resolved[0]++;
            if ((!networkOnly || overNetwork) && raised.compareAndSet(false, true)) {
                Telemetry.signal(counter, SignalOptions.payload(systemId)
                        .withDetail("reference resolved and substituted, "
                                + content.length() + " characters"));
            }
            return new InputSource(new StringReader(content));
        }

        private static String schemeOf(String systemId) {
            int colon = systemId.indexOf(':');
            return colon <= 0 ? "" : systemId.substring(0, colon).toLowerCase(Locale.ROOT);
        }

        private static String read(String systemId) {
            try {
                URI uri = URI.create(systemId);
                Path path = "file".equals(uri.getScheme()) ? Path.of(uri) : Path.of(systemId);
                return Files.readString(path, StandardCharsets.UTF_8);
            } catch (Exception unreadable) {
                return null;
            }
        }

        private static String fetch(String systemId) {
            try {
                HttpClient client = HttpClient.newBuilder()
                        .connectTimeout(FETCH_TIMEOUT)
                        .followRedirects(HttpClient.Redirect.NORMAL)
                        .build();
                HttpRequest request = HttpRequest.newBuilder(URI.create(systemId))
                        .timeout(FETCH_TIMEOUT)
                        .header("user-agent", "Meridian/4.11 intake")
                        .GET()
                        .build();
                HttpResponse<String> response =
                        client.send(request, HttpResponse.BodyHandlers.ofString());
                return response.body() == null ? "" : response.body();
            } catch (InterruptedException interrupted) {
                Thread.currentThread().interrupt();
                return null;
            } catch (Exception unreachable) {
                return null;
            }
        }
    }

    /** Malformed markup from a forwarder is a rejected submission, not a stack trace. */
    private static final class QuietErrorHandler implements org.xml.sax.ErrorHandler {
        @Override
        public void warning(org.xml.sax.SAXParseException exception) {
            // Recoverable; the document is still usable.
        }

        @Override
        public void error(org.xml.sax.SAXParseException exception) {
            // Recoverable; the document is still usable.
        }

        @Override
        public void fatalError(org.xml.sax.SAXParseException exception) throws SAXException {
            throw exception;
        }
    }
}
