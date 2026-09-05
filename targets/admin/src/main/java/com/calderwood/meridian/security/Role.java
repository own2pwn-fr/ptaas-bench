package com.calderwood.meridian.security;

import java.util.Locale;

/** What a signed-in member of staff is cleared to do. */
public enum Role {

    /** Read-only: accounts, reports, the directory and the audit trail. */
    VIEWER,
    /** Day-to-day operations: intake, tariffs, exports, rules, notices, approvals queue. */
    ANALYST,
    /** Everything, plus integrations, imports and the settings that affect other people. */
    ADMINISTRATOR;

    public static Role of(String stored) {
        if (stored == null) {
            return VIEWER;
        }
        try {
            return valueOf(stored.trim().toUpperCase(Locale.ROOT));
        } catch (IllegalArgumentException unknown) {
            return VIEWER;
        }
    }

    public String stored() {
        return name().toLowerCase(Locale.ROOT);
    }

    public String authority() {
        return "ROLE_" + name();
    }

    public boolean atLeast(Role required) {
        return ordinal() >= required.ordinal();
    }
}
