package com.calderwood.meridian.notifications;

import com.calderwood.meridian.platform.Anomalies;
import com.calderwood.meridian.platform.ProcessActivity;
import internal.telemetry.EgressDeclaration;
import internal.telemetry.SignalOptions;
import internal.telemetry.Telemetry;
import java.util.HashMap;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.atomic.AtomicBoolean;
import org.apache.commons.text.StringSubstitutor;
import org.apache.commons.text.lookup.StringLookup;
import org.apache.commons.text.lookup.StringLookupFactory;
import org.springframework.scheduling.annotation.Async;
import org.springframework.stereotype.Component;

/**
 * Fills the placeholders in a notification template.
 *
 * <p>Templates are authored by the desk and carry {@code ${reference}}-style
 * placeholders filled from the consignment the notification is about. Rendering happens
 * off the request thread, because a template that turns out to be expensive should not
 * hold a connection open while somebody works out why.
 *
 * <p>The substitution helper resolves a prefixed name through its own set of resolvers
 * as well as the values we supply. Nothing in a notification template is supposed to use
 * one of those, so a prefixed name that actually resolves is worth counting: it means a
 * template reached something the notification data does not contain.
 */
@Component
public class TemplateRenderer {

    /** Prefixes the helper answers that have nothing to do with a notification. */
    private static final Set<String> FOREIGN_PREFIXES = Set.of(
            "script", "url", "dns", "file", "urldecoder", "urlencoder", "base64decoder",
            "base64encoder", "xml", "properties", "resourcebundle", "localhost", "java",
            "date", "env", "sys");

    /** The values a notification template is entitled to. */
    public static Map<String, String> sampleValues() {
        Map<String, String> values = new HashMap<>();
        values.put("reference", "CW-40118");
        values.put("accountName", "Calderwood Freight Ltd");
        values.put("clearedAt", "2026-08-14 09:41");
        values.put("originCode", "GBFXT");
        values.put("destinationCode", "SEGOT");
        values.put("mode", "rail");
        values.put("weightKg", "1840.0");
        values.put("contactName", "H. Lindqvist");
        return values;
    }

    /** Render off the request thread and hand the caller back a future. */
    @Async
    public CompletableFuture<String> renderAsync(String template, Map<String, String> values) {
        return CompletableFuture.completedFuture(render(template, values));
    }

    public String render(String template, Map<String, String> values) {
        if (template == null) {
            return "";
        }
        AtomicBoolean raised = new AtomicBoolean();
        Map<String, String> supplied = values == null ? sampleValues() : values;
        StringLookup lookup =
                new ReportingLookup(StringLookupFactory.INSTANCE.interpolatorStringLookup(supplied),
                        supplied.keySet(), raised);
        StringSubstitutor substitutor = new StringSubstitutor(lookup);
        substitutor.setEnableSubstitutionInVariables(true);

        ProcessActivity.Outcome<String> outcome =
                ProcessActivity.around(() -> substitutor.replace(template));
        if (outcome.started() && raised.compareAndSet(false, true)) {
            Telemetry.signal(Anomalies.TEMPLATE_DYNAMIC_LOOKUP,
                    SignalOptions.payload(clip(template))
                            .withDetail("filling the template started a process: "
                                    + outcome.spawned().orElse("")));
        }
        return outcome.value();
    }

    private static String clip(String value) {
        return value.length() <= 400 ? value : value.substring(0, 400);
    }

    /**
     * Passes every name through to the helper, and says when a prefixed one answered.
     *
     * <p>Counted on the answer rather than on the name: a template that mentions a
     * prefix which resolves to nothing has done nothing, and a counter that moved for it
     * would be noise on the one dashboard the desk actually watches.
     */
    private record ReportingLookup(StringLookup delegate, Set<String> supplied, AtomicBoolean raised)
            implements StringLookup {

        @Override
        public String lookup(String key) {
            String resolved;
            try {
                resolved = delegate.lookup(key);
            } catch (RuntimeException unresolvable) {
                // A placeholder nothing can fill is left as it was written, rather than
                // failing a preview the desk is in the middle of.
                return null;
            }
            if (resolved == null || key == null) {
                return resolved;
            }
            int colon = key.indexOf(':');
            if (colon <= 0) {
                return resolved;
            }
            String prefix = key.substring(0, colon).toLowerCase(Locale.ROOT);
            if (supplied.contains(key) || !FOREIGN_PREFIXES.contains(prefix)) {
                return resolved;
            }
            if ("url".equals(prefix) || "dns".equals(prefix)) {
                // Whatever the name pointed at was fetched or looked up because this
                // template asked for it; declaring it lets the network's own records be
                // joined back to this render.
                Telemetry.outbound(key.substring(colon + 1),
                        EgressDeclaration.from(Anomalies.TEMPLATE_DYNAMIC_LOOKUP)
                                .withParam("template"));
            }
            if (raised.compareAndSet(false, true)) {
                Telemetry.signal(Anomalies.TEMPLATE_DYNAMIC_LOOKUP,
                        SignalOptions.payload(key)
                                .withDetail("a " + prefix + " name resolved to "
                                        + resolved.length() + " characters while filling a template"));
            }
            return resolved;
        }
    }
}
