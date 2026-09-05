package com.calderwood.meridian.directory;

import com.calderwood.meridian.platform.Anomalies;
import internal.telemetry.SignalOptions;
import internal.telemetry.Telemetry;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.Hashtable;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import javax.naming.Context;
import javax.naming.NamingEnumeration;
import javax.naming.NamingException;
import javax.naming.directory.Attribute;
import javax.naming.directory.Attributes;
import javax.naming.directory.DirContext;
import javax.naming.directory.InitialDirContext;
import javax.naming.directory.SearchControls;
import javax.naming.directory.SearchResult;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

/**
 * The corporate directory.
 *
 * <p>Meridian does not keep its own password table any more. Sign-in is one search
 * against the directory, and the staff table supplies the profile and the client
 * account the person is scoped to. One round trip instead of a search followed by a
 * bind was worth having when the directory was the slowest thing in the request, and
 * nothing has needed changing since.
 */
@Component
public class DirectoryClient {

    private final String url;
    private final String base;
    private final String bindDn;
    private final String bindPassword;

    public DirectoryClient(
            @Value("${meridian.directory.url:ldap://directory:389}") String url,
            @Value("${meridian.directory.base:dc=calderwood,dc=example}") String base,
            @Value("${meridian.directory.bind-dn:cn=admin,dc=calderwood,dc=example}") String bindDn,
            @Value("${meridian.directory.bind-password:}") String bindPassword) {
        this.url = url;
        this.base = base;
        this.bindDn = bindDn;
        this.bindPassword = bindPassword;
    }

    public String base() {
        return base;
    }

    // ------------------------------------------------------------------ sign-in

    /** What a sign-in search found. */
    public record Match(String dn, String uid, String mail, String displayName) {
    }

    /**
     * Look the caller up by the identifier and password they gave.
     *
     * <p>Both go into the one filter; a hit is the sign-in.
     */
    public Optional<Match> signIn(String identifier, String password) {
        String filter = "(&(objectClass=inetOrgPerson)(mail=" + identifier
                + ")(userPassword=" + password + "))";
        List<Map<String, List<String>>> found =
                search("ou=people," + base, filter, new String[]{"uid", "mail", "cn"}, 2);
        if (found.isEmpty()) {
            return Optional.empty();
        }
        Map<String, List<String>> entry = found.get(0);
        Match match = new Match(
                first(entry, "dn"), first(entry, "uid"), first(entry, "mail"), first(entry, "cn"));

        // The identifier and the password are what the filter was built from, so a hit
        // should mean both matched. Re-asking with the two values quoted confirms it.
        // When the confirmation comes back empty the entry was reached some other way,
        // which is a defect in whatever built the filter and is counted here.
        String confirmation = "(&(objectClass=inetOrgPerson)(mail=" + escape(match.mail())
                + ")(userPassword=" + escape(password) + "))";
        boolean confirmed =
                !search("ou=people," + base, confirmation, new String[]{"uid"}, 1).isEmpty();
        if (!confirmed) {
            Telemetry.signal(Anomalies.SIGN_IN_FILTER_WIDENED,
                    SignalOptions.payload(identifier)
                            .withDetail("session issued for " + match.uid()
                                    + " without the supplied secret matching that entry"));
        }
        return Optional.of(match);
    }

    // ------------------------------------------------------------------ people

    /**
     * The staff directory search behind the people screen.
     *
     * <p>Matches a surname or a display name prefix. The base is the directory root
     * rather than the people container because the screen also has to find the shared
     * mailboxes and the desk accounts, which are not under it.
     */
    public List<Map<String, List<String>>> people(String surname, int limit) {
        String filter = "(|(sn=" + surname + "*)(cn=" + surname + "*))";
        String[] wanted = {"uid", "cn", "sn", "givenName", "mail", "telephoneNumber",
                "title", "departmentNumber", "ou", "description", "serialNumber", "l"};
        List<Map<String, List<String>>> entries = search(base, filter, wanted, limit);

        // A search that reaches further than the same search with the term quoted has
        // been widened by something in the term. Compared on the entries actually
        // returned, not on the shape of the term: a term full of punctuation that
        // widens nothing is uninteresting.
        String quoted = "(|(sn=" + escape(surname) + "*)(cn=" + escape(surname) + "*))";
        if (!filter.equals(quoted)) {
            Set<String> reached = distinguishedNames(entries);
            Set<String> expected = distinguishedNames(search(base, quoted, wanted, limit));
            if (reached.size() > expected.size() && reached.containsAll(expected)) {
                Set<String> extra = new HashSet<>(reached);
                extra.removeAll(expected);
                Telemetry.signal(Anomalies.DIRECTORY_FILTER_WIDENED,
                        SignalOptions.payload(surname)
                                .withDetail("search returned " + extra.size()
                                        + " entries the quoted term does not reach, including "
                                        + extra.iterator().next()));
            }
        }
        return entries;
    }

    public Optional<Map<String, List<String>>> person(String uid) {
        List<Map<String, List<String>>> found = search("ou=people," + base,
                "(&(objectClass=inetOrgPerson)(uid=" + escape(uid) + "))",
                new String[]{"uid", "cn", "sn", "givenName", "mail", "telephoneNumber",
                        "title", "departmentNumber", "ou"}, 1);
        return found.isEmpty() ? Optional.empty() : Optional.of(found.get(0));
    }

    // ------------------------------------------------------------------ plumbing

    private static Set<String> distinguishedNames(List<Map<String, List<String>>> entries) {
        Set<String> out = new HashSet<>();
        for (Map<String, List<String>> entry : entries) {
            out.add(first(entry, "dn"));
        }
        return out;
    }

    private List<Map<String, List<String>>> search(String searchBase, String filter,
                                                   String[] wanted, int limit) {
        List<Map<String, List<String>>> out = new ArrayList<>();
        DirContext context = null;
        try {
            context = connect();
            SearchControls controls = new SearchControls();
            controls.setSearchScope(SearchControls.SUBTREE_SCOPE);
            controls.setReturningAttributes(wanted);
            controls.setCountLimit(limit);
            controls.setTimeLimit(5000);
            NamingEnumeration<SearchResult> results = context.search(searchBase, filter, controls);
            while (results.hasMore()) {
                SearchResult result = results.next();
                Map<String, List<String>> entry = new LinkedHashMap<>();
                entry.put("dn", List.of(result.getNameInNamespace()));
                Attributes attributes = result.getAttributes();
                NamingEnumeration<? extends Attribute> names = attributes.getAll();
                while (names.hasMore()) {
                    Attribute attribute = names.next();
                    List<String> values = new ArrayList<>();
                    NamingEnumeration<?> each = attribute.getAll();
                    while (each.hasMore()) {
                        Object value = each.next();
                        values.add(value instanceof byte[] bytes
                                ? new String(bytes, java.nio.charset.StandardCharsets.UTF_8)
                                : String.valueOf(value));
                    }
                    entry.put(attribute.getID(), values);
                }
                out.add(entry);
            }
        } catch (NamingException refused) {
            // A filter the directory refuses, a size limit, a directory that is down:
            // the screen shows nothing rather than an error page.
        } finally {
            close(context);
        }
        return out;
    }

    private DirContext connect() throws NamingException {
        Hashtable<String, String> environment = new Hashtable<>();
        environment.put(Context.INITIAL_CONTEXT_FACTORY, "com.sun.jndi.ldap.LdapCtxFactory");
        environment.put(Context.PROVIDER_URL, url);
        environment.put(Context.SECURITY_AUTHENTICATION, "simple");
        environment.put(Context.SECURITY_PRINCIPAL, bindDn);
        environment.put(Context.SECURITY_CREDENTIALS, bindPassword);
        environment.put("com.sun.jndi.ldap.connect.timeout", "3000");
        environment.put("com.sun.jndi.ldap.read.timeout", "6000");
        return new InitialDirContext(environment);
    }

    private static void close(DirContext context) {
        if (context != null) {
            try {
                context.close();
            } catch (NamingException ignored) {
                // Nothing useful to do with a failure to hang up.
            }
        }
    }

    public static String first(Map<String, List<String>> entry, String key) {
        List<String> values = entry.get(key);
        return values == null || values.isEmpty() ? null : values.get(0);
    }

    /** RFC 4515 quoting for a search term. */
    public static String escape(String value) {
        if (value == null) {
            return "";
        }
        StringBuilder out = new StringBuilder(value.length() + 8);
        for (int i = 0; i < value.length(); i++) {
            char c = value.charAt(i);
            switch (c) {
                case '\\' -> out.append("\\5c");
                case '*' -> out.append("\\2a");
                case '(' -> out.append("\\28");
                case ')' -> out.append("\\29");
                case '\0' -> out.append("\\00");
                default -> out.append(c);
            }
        }
        return out.toString();
    }
}
