package com.calderwood.meridian.platform;

import com.calderwood.meridian.security.Role;
import internal.telemetry.SignalOptions;
import internal.telemetry.Telemetry;
import java.util.Collection;
import java.util.LinkedHashSet;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

/**
 * Which staff properties may leave the service, and for whom.
 *
 * <p>Personnel data on a staff record — the national identifier, the pay band, the
 * recovery secret — is administrator-only under the group's data handling policy. The
 * check is applied to what a response actually carried rather than to what a caller
 * asked for, because those two are not the same thing: asking for a property and being
 * given nothing is not a disclosure, and a property arriving without being asked for is.
 */
public final class RestrictedFields {

    private static final Set<String> WATCHED = Set.of(
            "nationalid", "national_id",
            "payband", "pay_band",
            "recoverysecret", "recovery_secret",
            "passwordhash", "password_hash",
            "mfasecret", "mfa_secret");

    private RestrictedFields() {
    }

    public static boolean isRestricted(String property) {
        return property != null && WATCHED.contains(property.toLowerCase(Locale.ROOT));
    }

    /**
     * Inspect a payload on its way out.
     *
     * @param counter the counter to raise when something restricted really went out
     * @param served  the object about to be serialised
     * @param role    the clearance of the caller being served
     * @param payload what the caller asked for, recorded alongside
     */
    public static void inspect(String counter, Object served, Role role, String payload) {
        if (role == Role.ADMINISTRATOR) {
            return;
        }
        Set<String> carried = new LinkedHashSet<>();
        walk(served, carried, 0);
        if (carried.isEmpty()) {
            return;
        }
        Telemetry.signal(counter, SignalOptions.payload(payload)
                .withDetail("response served " + String.join(", ", carried)
                        + " to a caller holding " + role.stored()));
    }

    private static void walk(Object value, Set<String> carried, int depth) {
        if (value == null || depth > 8) {
            return;
        }
        if (value instanceof Map<?, ?> map) {
            for (Map.Entry<?, ?> entry : map.entrySet()) {
                String key = String.valueOf(entry.getKey());
                if (entry.getValue() != null && isRestricted(key)) {
                    carried.add(key);
                }
                walk(entry.getValue(), carried, depth + 1);
            }
            return;
        }
        if (value instanceof Collection<?> items) {
            for (Object item : items) {
                walk(item, carried, depth + 1);
            }
        }
    }
}
