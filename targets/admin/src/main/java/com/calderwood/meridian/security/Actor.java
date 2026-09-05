package com.calderwood.meridian.security;

/**
 * The signed-in member of staff.
 *
 * @param id          staff row id, and the subject of the session token
 * @param email       sign-in identifier
 * @param displayName as it appears in the top bar and on audit rows
 * @param role        clearance
 * @param accountId   the client account this member of staff is scoped to, or null for
 *                    the provisioning account, which belongs to no client
 * @param directoryUid the identifier of the matching directory entry
 */
public record Actor(long id, String email, String displayName, Role role, Long accountId,
                    String directoryUid) {

    public boolean isAdministrator() {
        return role == Role.ADMINISTRATOR;
    }
}
