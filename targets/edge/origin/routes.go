package main

// Storefront pages and the small API surface this tier still owns.
//
// Most of the catalogue is static and served from the bucket; what is left here is
// the handful of endpoints that need a session or a request body. Two of them also
// feed the cache coherence counters, because they are the ones that put
// request-derived content into a response the cache is allowed to keep.

import (
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"html"
	"net/url"
	"os"
	"strconv"
	"strings"
	"sync"
)

// Cache coherence counter names. Same contract as the framing counters in wire.go:
// dashboards key on these strings.
const (
	sigCacheHostVariance  = "edge.cache.coherence.host_variance"
	sigCacheParamVariance = "edge.cache.coherence.parameter_variance"
	sigCacheIdentityVar   = "edge.cache.coherence.identity_variance"
	sigLinkAuthorityBody  = "edge.links.authority.body_variance"
	sigLinkAuthorityLoc   = "edge.links.authority.location_variance"
)

// Extensions the cache tier treats as public assets. Kept in step with the matching
// list in the cache config; when they drift, personalised pages get cached.
var staticExts = []string{".css", ".js", ".png", ".jpg", ".ico", ".svg", ".txt", ".woff2"}

func hasStaticExt(p string) bool {
	lower := strings.ToLower(p)
	for _, e := range staticExts {
		if strings.HasSuffix(lower, e) {
			return true
		}
	}
	return false
}

// ---------------------------------------------------------------------------
// deployment identity
// ---------------------------------------------------------------------------

type siteConfig struct {
	Name          string
	Domain        string
	CanonicalHost string
}

// loadSite derives the storefront's name, domain and seeded account data from
// DEPLOY_SEED so that two deployments of the same image do not share content. Each
// piece can be overridden individually by an environment variable.
func loadSite() siteConfig {
	seed := os.Getenv("DEPLOY_SEED")
	name, domain := derivedIdentity(seed)
	if v := os.Getenv("SITE_NAME"); v != "" {
		name = v
	}
	if v := os.Getenv("SITE_DOMAIN"); v != "" {
		domain = v
	}
	cfg := siteConfig{
		Name:          name,
		Domain:        domain,
		CanonicalHost: envOr("SITE_CANONICAL_HOST", "www."+domain),
	}
	initAccounts(seed, domain)
	return cfg
}

func derivedIdentity(seed string) (string, string) {
	if seed == "" {
		return "Halyard Supply Co.", "halyardsupply.net"
	}
	sum := sha256.Sum256([]byte(seed + "|site"))
	first := []string{"Halyard", "Kestrel", "Meridian", "Larkspur", "Fathom", "Ridgeline", "Tallow", "Marlin"}
	second := []string{"Supply", "Outfitters", "Trading", "Provisions", "Goods", "Works"}
	a := first[int(sum[0])%len(first)]
	b := second[int(sum[1])%len(second)]
	return a + " " + b + " Co.", strings.ToLower(a+b) + ".net"
}

// ---------------------------------------------------------------------------
// accounts and sessions
// ---------------------------------------------------------------------------

type account struct {
	Login    string
	Display  string
	Password string
	Token    string // personal access token shown on the account page
}

var (
	accountsMu   sync.RWMutex
	accountsBy   = map[string]*account{}
	accountOrder []string

	sessionMu sync.Mutex
	sessions  = map[string]string{} // session id -> login
)

// Account tokens are derived from DEPLOY_SEED rather than generated at start-up. A
// token that changed on every restart would make the account page differ between two
// otherwise identical deployments, and the state digest printed by state-reset would
// never settle.
func initAccounts(seed, domain string) {
	// Logins and passwords are fixed: they are shared with the support desk and with
	// the release checks, so they cannot move between deployments. The API token is
	// the part that must not be predictable from anything public, so that one is
	// derived from the seed.
	people := []struct{ login, display, password string }{
		{"n.okonkwo", "Nadia Okonkwo", "harbour-mist-4417"},
		{"d.reyes", "Diego Reyes", "steel-cleat-2290"},
	}
	accountsMu.Lock()
	defer accountsMu.Unlock()
	accountsBy = map[string]*account{}
	accountOrder = nil
	for _, p := range people {
		login := p.login + "@" + domain
		sum := sha256.Sum256([]byte(seed + "|pat|" + login))
		accountsBy[login] = &account{
			Login: login, Display: p.display, Password: p.password,
			Token: "pat_" + hex.EncodeToString(sum[:16]),
		}
		accountOrder = append(accountOrder, login)
	}
}

func lookupAccount(login string) *account {
	accountsMu.RLock()
	defer accountsMu.RUnlock()
	return accountsBy[login]
}

// Two accounts, no registration: this tier is not the identity service, it only
// validates against the seeded set.
func checkPassword(login, password string) bool {
	a := lookupAccount(login)
	return a != nil && a.Password != "" && password == a.Password
}

func newSession(login string) string {
	b := make([]byte, 16)
	_, _ = rand.Read(b)
	sid := hex.EncodeToString(b)
	sessionMu.Lock()
	sessions[sid] = login
	sessionMu.Unlock()
	return sid
}

func sessionUser(cookie string) string {
	for _, part := range strings.Split(cookie, ";") {
		part = strings.TrimSpace(part)
		if !strings.HasPrefix(part, "sid=") {
			continue
		}
		sessionMu.Lock()
		login := sessions[strings.TrimPrefix(part, "sid=")]
		sessionMu.Unlock()
		if login != "" {
			return login
		}
	}
	return ""
}

func clearSessions() int {
	sessionMu.Lock()
	n := len(sessions)
	sessions = map[string]string{}
	sessionMu.Unlock()
	return n
}

func sessionCount() int {
	sessionMu.Lock()
	defer sessionMu.Unlock()
	return len(sessions)
}

// ---------------------------------------------------------------------------
// routing
// ---------------------------------------------------------------------------

func splitTarget(target string) (string, url.Values) {
	t := target
	if i := strings.Index(t, "://"); i >= 0 { // absolute-form targets are legal
		if j := strings.IndexByte(t[i+3:], '/'); j >= 0 {
			t = t[i+3+j:]
		} else {
			t = "/"
		}
	}
	q := url.Values{}
	if i := strings.IndexByte(t, '?'); i >= 0 {
		q, _ = url.ParseQuery(t[i+1:])
		t = t[:i]
	}
	return t, q
}

// pathAndQuery normalises a target to the origin-form a second client must send to
// land on the same cached object.
func pathAndQuery(target string) string {
	t := target
	if i := strings.Index(t, "://"); i >= 0 {
		if j := strings.IndexByte(t[i+3:], '/'); j >= 0 {
			t = t[i+3+j:]
		} else {
			t = "/"
		}
	}
	if !strings.HasPrefix(t, "/") {
		t = "/" + t
	}
	return t
}

// stripParam removes one query parameter, matching the normalisation the cache tier
// applies to the key for /news. The two must agree or the coherence probe silently
// looks at the wrong object.
func stripParam(target, name string) string {
	t := pathAndQuery(target)
	i := strings.IndexByte(t, '?')
	if i < 0 {
		return t
	}
	base, qs := t[:i], t[i+1:]
	var kept []string
	for _, kv := range strings.Split(qs, "&") {
		if kv == "" {
			continue
		}
		k := kv
		if j := strings.IndexByte(kv, '='); j >= 0 {
			k = kv[:j]
		}
		if k != name {
			kept = append(kept, kv)
		}
	}
	if len(kept) == 0 {
		return base
	}
	return base + "?" + strings.Join(kept, "&")
}

// Ordinary content pages. They carry no request-derived input at all, which is what
// makes them the right denominator when someone asks how noisy a scan of this tier is.
var staticPages = map[string]string{
	"/about":    "About us",
	"/contact":  "Contact us",
	"/delivery": "Delivery",
	"/returns":  "Returns and repairs",
	"/terms":    "Terms of sale",
	"/privacy":  "Privacy",
	"/stores":   "Our stores",
}

func matchRoute(req *wireRequest) (string, map[string]string) {
	p, _ := splitTarget(req.Target)
	vars := map[string]string{}
	if _, ok := staticPages[p]; ok {
		return p, vars
	}
	switch {
	case p == "/" || p == "":
		return "/", vars
	case p == "/status":
		return "/status", vars
	case p == "/healthz":
		return "/healthz", vars
	case p == "/robots.txt":
		return "/robots.txt", vars
	case p == "/sitemap.xml":
		return "/sitemap.xml", vars
	case p == "/products":
		return "/products", vars
	case strings.HasPrefix(p, "/products/"):
		vars["id"] = strings.TrimPrefix(p, "/products/")
		return "/products/:id", vars
	case p == "/search":
		return "/search", vars
	case strings.HasPrefix(p, "/assets/"):
		vars["file"] = strings.TrimPrefix(p, "/assets/")
		return "/assets/:file", vars
	case p == "/account/login":
		return "/account/login", vars
	case p == "/account/preferences":
		return "/account/preferences", vars
	case p == "/account/profile":
		return "/account/profile", vars
	case strings.HasPrefix(p, "/account/profile."):
		vars["ext"] = strings.TrimPrefix(p, "/account/profile.")
		return "/account/profile.:ext", vars
	case p == "/account/reset":
		return "/account/reset", vars
	case p == "/api/cart/items":
		return "/api/cart/items", vars
	case strings.HasPrefix(p, "/api/cart/items/"):
		vars["id"] = strings.TrimPrefix(p, "/api/cart/items/")
		return "/api/cart/items/:id", vars
	case p == "/api/products":
		return "/api/products", vars
	case strings.HasPrefix(p, "/api/products/"):
		vars["id"] = strings.TrimPrefix(p, "/api/products/")
		return "/api/products/:id", vars
	case p == "/account/orders":
		return "/account/orders", vars
	case p == "/stores" || strings.HasPrefix(p, "/stores/"):
		if p != "/stores" {
			vars["id"] = strings.TrimPrefix(p, "/stores/")
			return "/stores/:id", vars
		}
		return "/stores", vars
	case p == "/api/reviews":
		return "/api/reviews", vars
	case p == "/api/v1/ping":
		return "/api/v1/ping", vars
	case p == "/go":
		return "/go", vars
	case p == "/promo":
		return "/promo", vars
	case p == "/news":
		return "/news", vars
	case p == "/submit":
		return "/submit", vars
	}
	return "<unmatched>", vars
}

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

func (s *server) page(title, body string) []byte {
	return []byte("<!doctype html>\n<html lang=en><head><meta charset=utf-8>" +
		"<title>" + html.EscapeString(title) + " | " + html.EscapeString(s.site.Name) + "</title>" +
		"<link rel=stylesheet href=/assets/style.css><link rel=icon href=/assets/favicon.ico>" +
		"</head><body>\n<header><a href=\"/\">" + html.EscapeString(s.site.Name) + "</a></header>\n" +
		body + "\n<footer>&copy; " + html.EscapeString(s.site.Name) + "</footer>" +
		"\n<script src=/assets/app.js></script></body></html>\n")
}

func htmlResp(status int, body []byte, extra ...[2]string) *response {
	h := [][2]string{{"Content-Type", "text/html; charset=utf-8"}}
	h = append(h, extra...)
	return &response{status: status, headers: h, body: body}
}

func jsonResp(status int, body string, extra ...[2]string) *response {
	h := [][2]string{{"Content-Type", "application/json"}}
	h = append(h, extra...)
	return &response{status: status, headers: h, body: []byte(body)}
}

// foreignAuthority reports an authority that is neither ours nor one of the service
// names inside the estate, i.e. one that can only have come from the request.
func (s *server) foreignAuthority(a string) bool {
	a = strings.ToLower(strings.TrimSpace(a))
	if a == "" {
		return false
	}
	if i := strings.LastIndexByte(a, ':'); i >= 0 {
		if _, err := strconv.Atoi(a[i+1:]); err == nil {
			a = a[:i]
		}
	}
	if a == strings.ToLower(s.site.CanonicalHost) || a == strings.ToLower(s.site.Domain) {
		return false
	}
	switch a {
	case "nginx", "haproxy", "varnish", "origin", "localhost", "127.0.0.1":
		return false
	}
	return true
}

// ---------------------------------------------------------------------------
// handlers
// ---------------------------------------------------------------------------

func (s *server) handle(cs *connState, req *wireRequest) *response {
	route, vars := matchRoute(req)
	p, q := splitTarget(req.Target)
	reqID := cs.id + "/" + strconv.Itoa(cs.reqIndex)
	login := sessionUser(req.Header("Cookie"))

	if title, ok := staticPages[route]; ok {
		return htmlResp(200, s.page(title, "<h1>"+html.EscapeString(title)+
			"</h1><p>Everything you need to know, and a phone number if it is not here.</p>"),
			[2]string{"Cache-Control", "public, max-age=300"})
	}

	switch route {

	case "/":
		return htmlResp(200, s.page("Home", `<h1>Rope, rigging and deck hardware</h1>
<ul>
<li><a href="/products">Catalogue</a></li>
<li><a href="/search?q=shackle">Search</a></li>
<li><a href="/promo">This month's offers</a></li>
<li><a href="/news?lang=en">Workshop notes</a></li>
<li><a href="/account/login">Sign in</a></li>
<li><a href="/account/reset">Forgotten your password?</a></li>
<li><a href="/go?to=/promo">Partner offer</a></li>
<li><a href="/status">Service status</a></li>
</ul>
<h2>Ask us a question</h2>
<form method="post" action="/submit">
<input name="note" value="How long is delivery to the islands?">
<button>Send</button></form>`))

	case "/status":
		return htmlResp(200, s.page("Service status", "<h1>Service status</h1><p>All systems normal.</p>"))

	case "/healthz":
		return &response{status: 200, headers: [][2]string{{"Content-Type", "text/plain"}}, body: []byte("ok\n")}

	case "/robots.txt":
		return &response{status: 200, headers: [][2]string{{"Content-Type", "text/plain"}},
			body: []byte("User-agent: *\nAllow: /\nDisallow: /account/\nSitemap: https://" +
				s.site.CanonicalHost + "/sitemap.xml\n")}

	case "/sitemap.xml":
		var b strings.Builder
		b.WriteString(`<?xml version="1.0" encoding="UTF-8"?>` + "\n<urlset>\n")
		for _, u := range []string{"/", "/products", "/search", "/promo", "/news", "/account/login", "/account/reset", "/status"} {
			fmt.Fprintf(&b, "  <url><loc>https://%s%s</loc></url>\n", s.site.CanonicalHost, u)
		}
		b.WriteString("</urlset>\n")
		return &response{status: 200, headers: [][2]string{{"Content-Type", "application/xml"}}, body: []byte(b.String())}

	case "/products":
		var b strings.Builder
		b.WriteString("<h1>Catalogue</h1><ul>")
		for i := 1; i <= 6; i++ {
			fmt.Fprintf(&b, `<li><a href="/products/%d">Item %d</a></li>`, i, i)
		}
		b.WriteString("</ul>")
		return htmlResp(200, s.page("Catalogue", b.String()), [2]string{"Cache-Control", "public, max-age=30"})

	case "/products/:id":
		id := html.EscapeString(vars["id"])
		return htmlResp(200, s.page("Item "+id, "<h1>Item "+id+"</h1>"+
			`<form method="post" action="/api/cart/items"><button>Add to basket</button></form>`),
			[2]string{"Cache-Control", "public, max-age=30"})

	case "/search":
		return htmlResp(200, s.page("Search",
			"<h1>Search</h1><p>No results for "+html.EscapeString(q.Get("q"))+".</p>"))

	case "/assets/:file":
		ct := "text/plain"
		switch {
		case strings.HasSuffix(vars["file"], ".css"):
			ct = "text/css"
		case strings.HasSuffix(vars["file"], ".js"):
			ct = "application/javascript"
		}
		return &response{status: 200, headers: [][2]string{
			{"Content-Type", ct}, {"Cache-Control", "public, max-age=300"}},
			body: []byte("/* " + s.site.Name + " */\n")}

	case "/account/login":
		return s.login(req)

	case "/account/preferences":
		return s.preferences(req, login)

	case "/account/profile":
		return s.profile(cs, req, p, login, reqID, false)

	case "/account/profile.:ext":
		return s.profile(cs, req, p, login, reqID, true)

	case "/account/reset":
		return s.reset(cs, req, reqID)

	case "/api/cart/items":
		return jsonResp(201, `{"status":"added","lines":1}`, [2]string{"Cache-Control", "no-store"})

	case "/api/cart/items/:id":
		return jsonResp(200, `{"status":"updated","id":"`+html.EscapeString(vars["id"])+`"}`,
			[2]string{"Cache-Control", "no-store"})

	case "/api/reviews":
		return jsonResp(201, `{"status":"received","moderation":"pending"}`,
			[2]string{"Cache-Control", "no-store"})

	case "/api/products":
		return jsonResp(200, `{"items":[{"id":1},{"id":2},{"id":3},{"id":4},{"id":5},{"id":6}]}`,
			[2]string{"Cache-Control", "public, max-age=60"})

	case "/api/products/:id":
		return jsonResp(200, `{"id":"`+html.EscapeString(vars["id"])+`","in_stock":true}`,
			[2]string{"Cache-Control", "public, max-age=60"})

	case "/stores/:id":
		return htmlResp(200, s.page("Store", "<h1>Store "+html.EscapeString(vars["id"])+
			"</h1><p>Open Monday to Saturday.</p>"), [2]string{"Cache-Control", "public, max-age=300"})

	case "/account/orders":
		if login == "" {
			return htmlResp(401, s.page("Orders", "<h1>Please sign in</h1>"),
				[2]string{"Cache-Control", "no-store"})
		}
		return htmlResp(200, s.page("Orders", "<h1>Your orders</h1><p>Nothing on its way.</p>"),
			[2]string{"Cache-Control", "private, no-store"})

	case "/api/v1/ping":
		// Health probe for the mobile app's connectivity check. Not linked from
		// anywhere; the apps have the path compiled in.
		return jsonResp(200, `{"pong":true}`, [2]string{"Cache-Control", "no-store"})

	case "/go":
		return s.partnerRedirect(cs, req, q, reqID)

	case "/promo":
		return s.promo(cs, req, reqID)

	case "/news":
		return s.news(cs, req, q, reqID)

	case "/submit":
		return htmlResp(200, s.page("Thanks",
			fmt.Sprintf("<h1>Thanks</h1><p>We received %d characters and will reply within a day.</p>",
				len(req.Body))),
			[2]string{"Cache-Control", "no-store"})
	}

	return htmlResp(404, s.page("Not found", "<h1>Page not found</h1>"))
}

func (s *server) login(req *wireRequest) *response {
	if strings.EqualFold(req.Method, "POST") {
		form, _ := url.ParseQuery(string(req.Body))
		u := form.Get("email")
		if checkPassword(u, form.Get("password")) {
			return htmlResp(200, s.page("Signed in", "<h1>Welcome back</h1>"),
				[2]string{"Set-Cookie", "sid=" + newSession(u) + "; Path=/; HttpOnly"},
				[2]string{"Cache-Control", "no-store"})
		}
		return htmlResp(401, s.page("Sign in", "<h1>Those details did not match</h1>"),
			[2]string{"Cache-Control", "no-store"})
	}
	return htmlResp(200, s.page("Sign in", `<h1>Sign in</h1>
<form method="post" action="/account/login">
<input name="email" type="email" placeholder="you@example.com">
<input name="password" type="password">
<button>Sign in</button></form>`), [2]string{"Cache-Control", "no-store"})
}

// preferences is an ordinary settings form. It carries a body, which is the only
// reason it appears in the framing counters' entrypoint list.
func (s *server) preferences(req *wireRequest, login string) *response {
	if login == "" {
		return htmlResp(401, s.page("Preferences", "<h1>Please sign in</h1>"),
			[2]string{"Cache-Control", "no-store"})
	}
	if strings.EqualFold(req.Method, "POST") {
		form, _ := url.ParseQuery(string(req.Body))
		return htmlResp(200, s.page("Preferences",
			"<h1>Saved</h1><p>Newsletter: "+html.EscapeString(form.Get("newsletter"))+"</p>"),
			[2]string{"Cache-Control", "no-store"})
	}
	return htmlResp(200, s.page("Preferences", `<h1>Preferences</h1>
<form method="post" action="/account/preferences">
<input name="newsletter" value="weekly"><button>Save</button></form>`),
		[2]string{"Cache-Control", "no-store"})
}

// profile shows the signed-in customer's personal access token. When it is reached
// through a path that ends in an asset extension the cache tier will keep it, so the
// coherence probe re-fetches the same URL with no session and checks what comes back.
func (s *server) profile(cs *connState, req *wireRequest, path, login, reqID string, viaExt bool) *response {
	if login == "" {
		return htmlResp(401, s.page("Account", "<h1>Please sign in</h1>"),
			[2]string{"Cache-Control", "no-store"})
	}
	acct := lookupAccount(login)
	if acct == nil {
		return htmlResp(401, s.page("Account", "<h1>Please sign in</h1>"),
			[2]string{"Cache-Control", "no-store"})
	}
	body := s.page("Account", "<h1>"+html.EscapeString(acct.Display)+"</h1>"+
		"<p>Signed in as "+html.EscapeString(acct.Login)+"</p>"+
		"<p>API token: <code>"+acct.Token+"</code></p>")

	hdr := [][2]string{{"Content-Type", "text/html; charset=utf-8"}}
	if !viaExt {
		hdr = append(hdr, [2]string{"Cache-Control", "private, no-store"})
	} else if hasStaticExt(path) {
		s.probe.enqueue(probeJob{
			signal:   sigCacheIdentityVar,
			path:     pathAndQuery(req.Target),
			marker:   acct.Token,
			where:    "body",
			injector: "session:" + acct.Login,
			payload:  req.Method + " " + pathAndQuery(req.Target),
			detail: "a personalised account page was served under a path the cache tier treats as a public asset; " +
				"re-requesting the same URL with no session cookie",
			internal:  cs.internal,
			requestID: reqID,
			peer:      cs.peer,
			client:    cs.client,
		})
	}
	return &response{status: 200, headers: hdr, body: body}
}

// reset renders the password reset page, including the absolute link the email would
// contain. The authority comes from the request so that the staging and preview
// hostnames produce working links without a per-environment config.
func (s *server) reset(cs *connState, req *wireRequest, reqID string) *response {
	host := req.Header("Host")
	if xfh := req.Header("X-Forwarded-Host"); xfh != "" {
		host = xfh
	}
	if host == "" {
		host = s.site.CanonicalHost
	}
	link := "http://" + host + "/account/reset/confirm?token=RESET-TOKEN"
	body := s.page("Password reset", `<h1>Check your inbox</h1><p>We have sent you this link:</p><p><a href="`+
		link+`">`+html.EscapeString(link)+`</a></p>`)
	if s.foreignAuthority(host) {
		s.probe.enqueue(probeJob{
			signal:   sigLinkAuthorityBody,
			path:     pathAndQuery(req.Target),
			marker:   "http://" + host + "/account/reset/confirm",
			where:    "body",
			injector: cs.client,
			payload:  "Host: " + host,
			detail: "the reset link was built from the request's Host/X-Forwarded-Host (" + host +
				") rather than " + s.site.CanonicalHost + ", on a response the cache tier keeps",
			internal:  cs.internal,
			requestID: reqID,
			peer:      cs.peer,
			client:    cs.client,
		})
	}
	return htmlResp(200, body, [2]string{"Cache-Control", "public, max-age=30"})
}

// partnerRedirect bounces to a partner page. The authority is taken from
// X-Forwarded-Host so that the redirect stays inside whichever hostname the customer
// arrived on.
func (s *server) partnerRedirect(cs *connState, req *wireRequest, q url.Values, reqID string) *response {
	host := req.Header("X-Forwarded-Host")
	if host == "" {
		host = req.Header("Host")
	}
	if host == "" {
		host = s.site.CanonicalHost
	}
	to := q.Get("to")
	if to == "" || !strings.HasPrefix(to, "/") {
		to = "/promo"
	}
	loc := "http://" + host + to
	if s.foreignAuthority(host) {
		s.probe.enqueue(probeJob{
			signal:   sigLinkAuthorityLoc,
			path:     pathAndQuery(req.Target),
			marker:   "http://" + host,
			where:    "header:Location",
			injector: cs.client,
			payload:  "X-Forwarded-Host: " + host,
			detail: "a redirect the cache tier keeps was built with authority " + host +
				" taken from X-Forwarded-Host",
			internal:  cs.internal,
			requestID: reqID,
			peer:      cs.peer,
			client:    cs.client,
		})
	}
	return &response{status: 302, headers: [][2]string{
		{"Location", loc},
		{"Content-Type", "text/html; charset=utf-8"},
		{"Cache-Control", "public, max-age=30"},
	}, body: s.page("Redirecting", "<p>Taking you to our partner...</p>")}
}

// promo builds its asset URLs from X-Forwarded-Host so that the preview hostnames
// load their own copies of the images.
func (s *server) promo(cs *connState, req *wireRequest, reqID string) *response {
	host := req.Header("X-Forwarded-Host")
	if host == "" {
		host = s.site.CanonicalHost
	}
	asset := "http://" + host + "/assets/app.js"
	body := s.page("Offers", `<h1>This month's offers</h1><script src="`+asset+`"></script>
<p>Images served from `+html.EscapeString(host)+`.</p>`)
	if s.foreignAuthority(host) {
		s.probe.enqueue(probeJob{
			signal:   sigCacheHostVariance,
			path:     pathAndQuery(req.Target),
			marker:   asset,
			where:    "body",
			injector: cs.client,
			payload:  "X-Forwarded-Host: " + host,
			detail: "X-Forwarded-Host reached a cacheable body but is not part of the cache key; " +
				"re-requesting the same URL without it",
			internal:  cs.internal,
			requestID: reqID,
			peer:      cs.peer,
			client:    cs.client,
		})
	}
	return htmlResp(200, body, [2]string{"Cache-Control", "public, max-age=30"})
}

// news renders the workshop notes in the requested locale. The cache tier drops
// `lang` from the key, so the same object is shared across locales.
func (s *server) news(cs *connState, req *wireRequest, q url.Values, reqID string) *response {
	lang := q.Get("lang")
	if lang == "" {
		lang = "en"
	}
	body := s.page("Workshop notes", `<h1>Workshop notes</h1><p lang="`+
		html.EscapeString(lang)+`">Edition: `+lang+`</p>`)
	if lang != "en" {
		s.probe.enqueue(probeJob{
			signal: sigCacheParamVariance,
			// The same URL minus `lang`: a request nobody made, which the cache tier
			// nevertheless resolves to the same object.
			path:     stripParam(req.Target, "lang"),
			marker:   "Edition: " + lang,
			where:    "body",
			injector: cs.client,
			payload:  "?lang=" + lang,
			detail: "the locale parameter reached a cacheable body but is normalised out of the cache key; " +
				"re-requesting the same object without it",
			internal:  cs.internal,
			requestID: reqID,
			peer:      cs.peer,
			client:    cs.client,
		})
	}
	return htmlResp(200, body, [2]string{"Cache-Control", "public, max-age=30"})
}
