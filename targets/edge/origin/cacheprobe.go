package main

// Cache coherence probe.
//
// Seeing our own input echoed back in our own response proves nothing about the
// cache: the response we read may never have been stored, and if it was, it may have
// been stored under a key nobody else will ever ask for. The question that actually
// matters to the platform team is the next one — would somebody else get this? — and
// only a second request can answer it.
//
// So whenever this tier emits a cacheable response that carries content derived from
// the request (a host taken from a header, a locale taken from the query string, a
// signed-in customer's token), it queues a job here. A moment later, long enough for
// the response to have been stored, a worker asks for the same object through the
// front door as an unrelated client would:
//
//   * a brand-new connection, keep-alives off, so no state is shared;
//   * the canonical Host, no session cookie;
//   * none of the input that produced the original response.
//
// If the marker comes back on that request, the cache is serving one client's content
// to another and the counter goes up. If it does not, nothing is recorded, which is
// the correct answer and by far the common case.
//
// The probe never blocks a response: enqueue is non-blocking and a full queue drops
// the job. A missed count is cheap; added latency on this tier is not.

import (
	"bytes"
	"io"
	"log"
	"net"
	"net/http"
	"strconv"
	"strings"
	"sync"
	"time"
)

// probeUserAgent makes the probe's own requests recognisable in the access logs.
// Nothing branches on it: the probe cannot queue work for itself in any case, because
// it sends none of the input that queues work — no forwarded host, no locale, no
// session — so every one of its requests takes the ordinary path.
const probeUserAgent = "edge-cache-probe/1.2"

type probeJob struct {
	signal    string
	path      string // path (and query) to request as a clean client
	marker    string // request-derived content that must not reach an unrelated client
	where     string // "body" or "header:<Name>"
	injector  string // identity that produced the original response
	payload   string
	detail    string
	internal  bool
	requestID string
	peer      string
	client    string
}

type cacheProbe struct {
	s     *server
	base  string
	ch    chan probeJob
	cli   *http.Client
	delay time.Duration

	mu   sync.Mutex
	seen map[string]time.Time
}

func newCacheProbe(s *server) *cacheProbe {
	delayMS, err := strconv.Atoi(envOr("CACHE_PROBE_DELAY_MS", "400"))
	if err != nil || delayMS <= 0 {
		delayMS = 400
	}
	p := &cacheProbe{
		s:    s,
		base: strings.TrimRight(envOr("CACHE_PROBE_BASE_URL", "http://nginx:80"), "/"),
		ch:   make(chan probeJob, 512),
		cli: &http.Client{
			Timeout: 5 * time.Second,
			Transport: &http.Transport{
				// A fresh connection every time. Reusing the one that produced the
				// response would answer a different question than the one we asked.
				DisableKeepAlives:   true,
				DialContext:         (&net.Dialer{Timeout: 3 * time.Second}).DialContext,
				TLSHandshakeTimeout: 3 * time.Second,
			},
			// /go answers a redirect and the Location header is the evidence, so it
			// must not be followed.
			CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
		},
		delay: time.Duration(delayMS) * time.Millisecond,
		seen:  map[string]time.Time{},
	}
	for i := 0; i < 4; i++ {
		go p.worker()
	}
	return p
}

func (p *cacheProbe) enqueue(j probeJob) {
	key := j.signal + "|" + j.path + "|" + j.marker
	now := time.Now()
	p.mu.Lock()
	if last, ok := p.seen[key]; ok && now.Sub(last) < 10*time.Second {
		p.mu.Unlock()
		return
	}
	p.seen[key] = now
	if len(p.seen) > 4096 {
		for k, t := range p.seen {
			if now.Sub(t) > time.Minute {
				delete(p.seen, k)
			}
		}
	}
	p.mu.Unlock()

	select {
	case p.ch <- j:
	default:
		log.Printf("cache probe: queue full, dropping %s", j.signal)
	}
}

func (p *cacheProbe) pending() int {
	p.mu.Lock()
	defer p.mu.Unlock()
	return len(p.seen)
}

func (p *cacheProbe) reset() {
	p.mu.Lock()
	p.seen = map[string]time.Time{}
	p.mu.Unlock()
}

func (p *cacheProbe) worker() {
	for j := range p.ch {
		p.run(j)
	}
}

func (p *cacheProbe) run(j probeJob) {
	time.Sleep(p.delay) // let the original response reach the cache first

	req, err := http.NewRequest(http.MethodGet, p.base+j.path, nil)
	if err != nil {
		return
	}
	req.Host = p.s.site.CanonicalHost
	req.Header.Set("User-Agent", probeUserAgent)

	resp, err := p.cli.Do(req)
	if err != nil {
		return
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(io.LimitReader(resp.Body, 256<<10))

	var found bool
	var where string
	if strings.HasPrefix(j.where, "header:") {
		name := strings.TrimPrefix(j.where, "header:")
		val := resp.Header.Get(name)
		found = j.marker != "" && strings.Contains(val, j.marker)
		where = name + ": " + clip(val, 200)
	} else {
		found = j.marker != "" && bytes.Contains(body, []byte(j.marker))
		where = "response body (" + strconv.Itoa(len(body)) + " bytes)"
	}
	if !found {
		return
	}

	state := resp.Header.Get("X-Cache")
	if state == "" {
		state = "unknown"
	}
	detail := j.detail + " -- CONFIRMED: an unrelated client (new connection, Host=" +
		p.s.site.CanonicalHost + ", no session, none of the original input) received " +
		strconv.Quote(clip(j.marker, 120)) + " in " + where + "; cache state " + state +
		"; the original response was produced for " + j.injector

	p.s.raise(j.signal, j.internal, j.payload, detail, j.requestID, j.peer, j.client)
}
