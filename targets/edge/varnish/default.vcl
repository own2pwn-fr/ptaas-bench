vcl 4.0;

# Cache tier for the storefront.
#
# PINNED TO varnish 6.0.13 (6.0 LTS, see compose/edge.yml). VCL is not
# forward-compatible: `vcl 4.0` semantics, the builtin subroutine behaviour this file
# leans on, and beresp handling all shift in 7.x. A floating tag either fails to load
# this file or loads it with different defaults, and the second failure is the
# expensive one because nothing complains.

backend origin {
    .host = "origin";
    .port = "8080";
    .connect_timeout = 3s;
    .first_byte_timeout = 30s;
    .between_bytes_timeout = 30s;
}

# Only the estate may invalidate. The request never arrives from outside in any case:
# the balancer sends everything that is not GET or HEAD straight to the origin, so a
# customer's BAN cannot reach this tier at all. Belt and braces.
acl estate {
    "10.77.0.0"/24;
    "127.0.0.1";
}

sub vcl_recv {
    # Deployment tooling flushes the cache after a release so that a new build is not
    # shadowed by the previous one's objects.
    if (req.method == "BAN") {
        if (!client.ip ~ estate) {
            return (synth(405, "Not allowed"));
        }
        ban("obj.status != 0");
        return (synth(200, "Invalidated"));
    }

    # Assets are public by definition, so they are cached on the strength of the URL
    # alone rather than being passed through because a Cookie happens to be present.
    # This is what let us drop the CDN's asset plan; hit ratio on /assets went from
    # nothing to 98%.
    #
    # The Cookie is left in place on the way to the origin: some asset responses are
    # personalised (the account stylesheet carries the customer's density setting) and
    # stripping it here made them all render at the default.
    if (req.url ~ "(?i)\.(css|js|png|jpg|ico|svg|txt|woff2)(\?|$)") {
        return (hash);
    }

    if (req.method != "GET" && req.method != "HEAD") {
        return (pass);
    }
    if (req.http.Cookie ~ "sid=") {
        return (pass);
    }
    return (hash);
}

sub vcl_hash {
    # Single origin, single hostname in production, so the Host adds nothing to the
    # key and cost us a duplicate copy of every object per preview hostname. The
    # default vcl_hash would include it.
    #
    # The locale on the workshop notes is presentation-only and was fragmenting the
    # key badly (one copy per locale per article, hit ratio around 40%), so it is
    # normalised out. It is still forwarded to the origin, which renders the right
    # edition. The regsub afterwards puts the leading separator back so that the key
    # is the same whatever position the parameter occupied.
    #
    # The origin's cache coherence probe builds its clean URL with the same rule
    # (stripParam in origin/routes.go). If these two ever disagree the probe silently
    # inspects the wrong object.
    if (req.url ~ "^/news") {
        hash_data(regsub(regsuball(req.url, "[?&]lang=[^&]*", ""), "^([^?&]*)&", "\1?"));
    } else {
        hash_data(req.url);
    }
    return (lookup);
}

sub vcl_backend_response {
    if (bereq.url ~ "(?i)\.(css|js|png|jpg|ico|svg|txt|woff2)(\?|$)") {
        unset beresp.http.Set-Cookie;
        set beresp.http.Cache-Control = "public, max-age=60";
        set beresp.ttl = 60s;
        set beresp.grace = 0s;
        return (deliver);
    }

    # Partner redirects are stable for the length of a campaign and were the single
    # biggest source of origin traffic before we started keeping them.
    if (beresp.status == 301 || beresp.status == 302) {
        set beresp.ttl = 30s;
        set beresp.grace = 0s;
        return (deliver);
    }

    if (beresp.ttl <= 0s) {
        set beresp.ttl = 30s;
    }
    set beresp.grace = 0s;
    return (deliver);
}

sub vcl_deliver {
    if (obj.hits > 0) {
        set resp.http.X-Cache = "HIT";
    } else {
        set resp.http.X-Cache = "MISS";
    }
}
