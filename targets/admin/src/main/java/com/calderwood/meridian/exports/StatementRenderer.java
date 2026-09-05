package com.calderwood.meridian.exports;

import com.calderwood.meridian.platform.Anomalies;
import com.calderwood.meridian.platform.ProcessActivity;
import internal.telemetry.EgressDeclaration;
import internal.telemetry.SignalOptions;
import internal.telemetry.Telemetry;
import java.io.ByteArrayInputStream;
import java.io.File;
import java.io.StringReader;
import java.io.StringWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.Locale;
import java.util.concurrent.atomic.AtomicBoolean;
import javax.xml.transform.Source;
import javax.xml.transform.Transformer;
import javax.xml.transform.TransformerFactory;
import javax.xml.transform.URIResolver;
import javax.xml.transform.stream.StreamResult;
import javax.xml.transform.stream.StreamSource;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

/**
 * Renders an account statement through a stylesheet.
 *
 * <p>Two clients insisted on their own statement layout during onboarding, so the export
 * screen accepts either the name of a stored layout or one pasted in by the integration
 * team. The pasted form compiles with the processor's restrictions relaxed, because the
 * layouts those clients supplied use functions the strict profile refuses.
 */
@Component
public class StatementRenderer {

    private final File statementDirectory;
    private final File stylesheetDirectory;

    public StatementRenderer(
            @Value("${meridian.data.statements:/opt/meridian/data/statements}") String statements,
            @Value("${meridian.data.stylesheets:/opt/meridian/data/stylesheets}") String stylesheets) {
        this.statementDirectory = new File(statements);
        this.stylesheetDirectory = new File(stylesheets);
    }

    /** The layouts the desk can pick from. */
    public String[] stored() {
        String[] names = stylesheetDirectory.list((dir, name) -> name.endsWith(".xsl"));
        return names == null ? new String[0] : names;
    }

    public boolean hasStatement(String statementId) {
        return statementFile(statementId).isFile();
    }

    /**
     * Render one statement.
     *
     * @param statementId which statement
     * @param stylesheet  either the name of a stored layout, or a layout itself
     */
    public String render(String statementId, String stylesheet) throws Exception {
        File statement = statementFile(statementId);
        if (!statement.isFile()) {
            throw new IllegalArgumentException("no statement " + statementId);
        }
        String document = Files.readString(statement.toPath(), StandardCharsets.UTF_8);
        Source layout = layoutSource(stylesheet);

        AtomicBoolean raised = new AtomicBoolean();
        TransformerFactory factory = TransformerFactory.newInstance();
        factory.setFeature(javax.xml.XMLConstants.FEATURE_SECURE_PROCESSING, false);
        factory.setURIResolver(new ReportingResolver(raised));

        Transformer transformer = factory.newTransformer(layout);
        transformer.setURIResolver(new ReportingResolver(raised));

        StringWriter out = new StringWriter();
        ProcessActivity.Outcome<Void> outcome = ProcessActivity.around(() -> {
            try {
                transformer.transform(
                        new StreamSource(new ByteArrayInputStream(
                                document.getBytes(StandardCharsets.UTF_8))),
                        new StreamResult(out));
            } catch (Exception failed) {
                throw new IllegalStateException(failed.getMessage(), failed);
            }
        });
        if (outcome.started() && raised.compareAndSet(false, true)) {
            Telemetry.signal(Anomalies.EXPORT_STYLESHEET_EXTERNAL_CALL,
                    SignalOptions.payload(clip(stylesheet))
                            .withDetail("the transform started a process: "
                                    + outcome.spawned().orElse("")));
        }
        return out.toString();
    }

    private Source layoutSource(String stylesheet) throws Exception {
        if (stylesheet == null || stylesheet.isBlank()) {
            return new StreamSource(new File(stylesheetDirectory, "statement-a4.xsl"));
        }
        String trimmed = stylesheet.trim();
        if (!trimmed.startsWith("<")) {
            String name = trimmed.endsWith(".xsl") ? trimmed : trimmed + ".xsl";
            File stored = new File(stylesheetDirectory, new File(name).getName());
            if (!stored.isFile()) {
                throw new IllegalArgumentException("no layout " + trimmed);
            }
            return new StreamSource(stored);
        }
        return new StreamSource(new StringReader(trimmed));
    }

    private File statementFile(String statementId) {
        String name = "statement-" + new File(statementId == null ? "" : statementId).getName() + ".xml";
        return new File(statementDirectory, name);
    }

    private static String clip(String value) {
        if (value == null) {
            return "";
        }
        return value.length() <= 400 ? value : value.substring(0, 400);
    }

    /**
     * Fetches what a layout asks for, and says so.
     *
     * <p>A layout is supposed to format the statement it is given. One that goes and
     * gets something else is doing more than formatting, and the operations team wants
     * to know which one and where from.
     */
    private static final class ReportingResolver implements URIResolver {

        private final AtomicBoolean raised;

        ReportingResolver(AtomicBoolean raised) {
            this.raised = raised;
        }

        @Override
        public Source resolve(String href, String base) {
            if (href == null || href.isBlank()) {
                return null;
            }
            String scheme = scheme(href);
            boolean remote = "http".equals(scheme) || "https".equals(scheme) || "ftp".equals(scheme);
            if (remote) {
                Telemetry.outbound(href,
                        EgressDeclaration.from(Anomalies.EXPORT_STYLESHEET_EXTERNAL_CALL)
                                .withParam("stylesheet"));
            }
            if (raised.compareAndSet(false, true)) {
                Telemetry.signal(Anomalies.EXPORT_STYLESHEET_EXTERNAL_CALL,
                        SignalOptions.payload(href)
                                .withDetail("the transform resolved a resource named by the layout"));
            }
            // Let the processor fetch it the way it always has.
            return null;
        }

        private static String scheme(String href) {
            int colon = href.indexOf(':');
            return colon <= 0 ? "" : href.substring(0, colon).toLowerCase(Locale.ROOT);
        }
    }
}
