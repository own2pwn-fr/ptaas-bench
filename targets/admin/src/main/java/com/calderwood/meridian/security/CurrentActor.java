package com.calderwood.meridian.security;

import org.springframework.security.core.Authentication;
import org.springframework.security.core.context.SecurityContextHolder;

/** Reads the signed-in member of staff out of the security context. */
public final class CurrentActor {

    static final String ATTRIBUTE = "meridian.actor";

    private CurrentActor() {
    }

    /** The signed-in member of staff, or null on an anonymous request. */
    public static Actor get() {
        Authentication authentication = SecurityContextHolder.getContext().getAuthentication();
        Object principal = authentication == null ? null : authentication.getPrincipal();
        return principal instanceof Actor actor ? actor : null;
    }

    /** The signed-in member of staff, or a 401 if there is none. */
    public static Actor required() {
        Actor actor = get();
        if (actor == null) {
            throw new NotSignedIn();
        }
        return actor;
    }

    /** Raised when a handler needs an identity and the request has none. */
    public static final class NotSignedIn extends RuntimeException {
        private static final long serialVersionUID = 1L;
    }
}
