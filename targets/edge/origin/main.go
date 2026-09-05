package main

// Origin tier for the Halyard Supply storefront.
//
// It serves the small set of pages and API endpoints that are not handed to the
// static bucket, and it is the last hop behind the TCP front, the load balancer and
// the cache. Because it is last, it is also where the platform team's connection
// integrity and cache coherence counters are computed: it is the only process that
// sees each request exactly as the chain delivered it.
//
// WHERE THE CLIENT ADDRESS COMES FROM
// -----------------------------------
// The socket peer here is always one of our own hops, never a customer, so the peer
// address alone cannot tell internal traffic from public traffic. The chain is built
// so that one address can still be trusted end to end:
//
//   * the TCP front speaks PROXY protocol to the load balancer, so the balancer's
//     own socket view is the real client and not the front's address;
//   * the first hop that parses HTTP writes that view into X-Real-IP with a
//     *replacing* directive, so a client-supplied copy never survives;
//   * this process consults X-Real-IP only when its socket peer is itself one of our
//     hops, and otherwise falls back to the peer.
//
// The consequence worth remembering: X-Real-IP is trustworthy here because of who
// writes it and where we are standing, not because it is a header. Anywhere else in
// the estate, treat it as client input.
//
// Records therefore carry both: the address this tier established (client_ip), which
// is the customer, and the socket peer the bytes actually arrived from (peer_ip),
// which is whichever of our own hops handed them over. peer_ip is the only one of the
// two this process observed for itself rather than read out of a header, so it is what
// the platform's own tooling keys on when it has to tell records apart by where they
// physically came from; a record without it cannot be placed in the estate at all.

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"log"
	"net"
	"net/url"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"sync/atomic"
	"syscall"
	"time"
)

var connCounter atomic.Int64

type connState struct {
	id       string
	peer     string // socket peer host: one of our own hops
	client   string // address attested by the first HTTP-terminating hop
	internal bool   // client falls inside our own address range
	reqIndex int
	hopProto string // received-protocol of the nearest Via entry ("1.1", "2.0")
	watch    *frameWatch
}

type server struct {
	tel    *telemetry
	probe  *cacheProbe
	site   siteConfig
	listen string

	// Our own address range, and the range our hops live in. Both come from the
	// environment because they are deployment facts, not code facts.
	internalNets []*net.IPNet
	hopNets      []*net.IPNet
}

func main() {
	log.SetFlags(log.LstdFlags | log.Lmicroseconds)
	s := &server{
		tel:          newTelemetry(),
		site:         loadSite(),
		listen:       envOr("LISTEN_ADDR", ":8080"),
		internalNets: parseCIDRs(envOr("TELEMETRY_SYNTHETIC_CIDRS", "10.77.0.0/24")),
		hopNets:      parseCIDRs(envOr("TRUSTED_HOP_CIDRS", "10.77.0.0/24")),
	}
	s.probe = newCacheProbe(s)
	go s.serveControl()

	ln, err := net.Listen("tcp", s.listen)
	if err != nil {
		log.Fatalf("listen %s: %v", s.listen, err)
	}
	log.Printf("origin up on %s for %s", s.listen, s.site.CanonicalHost)

	stop := make(chan os.Signal, 1)
	signal.Notify(stop, syscall.SIGINT, syscall.SIGTERM)
	go func() {
		<-stop
		_ = ln.Close()
		s.tel.Close()
		os.Exit(0)
	}()

	for {
		nc, err := ln.Accept()
		if err != nil {
			log.Printf("accept: %v", err)
			return
		}
		go s.serveConn(nc)
	}
}

func (s *server) serveConn(nc net.Conn) {
	defer nc.Close()
	if tc, ok := nc.(*net.TCPConn); ok {
		_ = tc.SetKeepAlive(true)
	}
	w := newWireConn(nc)
	cs := &connState{
		id:   fmt.Sprintf("c%06d", connCounter.Add(1)),
		peer: hostOf(nc.RemoteAddr().String()),
	}
	for {
		req, err := readRequest(w)
		if err != nil {
			// A read failure ends the connection and is never counted as an anomaly:
			// mangled bytes say nothing, only a moved message boundary does.
			return
		}
		cs.reqIndex++
		cs.client = s.attestedClient(cs, req)
		cs.internal = ipInAny(cs.client, s.internalNets)

		// Judge the previous request's leftovers before touching this one: the verdict
		// depends on where this request's first byte landed.
		s.check(cs, req)

		if p := req.lastHopProto(); p != "" {
			cs.hopProto = p
		}

		resp := s.handle(cs, req)
		s.emitRequest(cs, req, resp.status)
		if err := writeResponse(w, req, resp); err != nil {
			return
		}
		cs.watch = frameCheck(req, cs.hopProto, cs.internal)
	}
}

// ---------------------------------------------------------------------------
// connection integrity verdict
// ---------------------------------------------------------------------------

// check compares what this process did with the previous request against what the
// hop in front of us must have done, using two independent tests. Either one alone
// is enough, and both are recorded.
//
//	OFFSET  - this request's first byte lies inside the range the other reading of the
//	          previous message still considered its body. Byte arithmetic, no
//	          interpretation.
//	VIA     - every hop appends a Via entry on the way through. A request with no Via
//	          at all did not pass through one, and the chain is the only route to this
//	          port, so it came out of the previous request's body.
//
// The Via test can be defeated by a sender that supplies its own Via, which makes the
// counter under-report. That is the safe direction and the one we chose.
func (s *server) check(cs *connState, req *wireRequest) {
	w := cs.watch
	cs.watch = nil
	if w == nil {
		return
	}

	offsetProof := w.altEnd > 0 && req.StartOffset < w.altEnd
	viaSeen := req.HasHeader("Via")
	if !offsetProof && viaSeen {
		return // ordinary pipelining on a keep-alive connection
	}

	// Attribution belongs to whoever sent the message that moved the boundary, which is
	// the request that armed the watch and not the one that came out of it. The request
	// that came out of it carries no attested client at all -- that is the whole point,
	// nobody sent it -- so this tier falls back to its socket peer for it, and that peer
	// is by construction one of our own hops. Folding that in would classify every
	// framing record as our own traffic no matter who caused it.
	internal := w.internal
	if w.signal == "" {
		s.tel.Emit(event{
			"type":      "note",
			"synthetic": internal,
			"peer_ip":   cs.peer,
			"client_ip": cs.client,
			"message": fmt.Sprintf("framing disagreement on %s (%s), not attributable to a known upstream: %s",
				cs.id, w.kind, w.note),
		})
		return
	}

	detail := fmt.Sprintf(
		"conn=%s req#%d kind=%s; unexpected request line %q; this process ended request #%d at offset %d, the hop in front ended it at %d (overlap %d bytes); Via present on the unexpected request: %v; %s",
		cs.id, cs.reqIndex, w.kind, strings.TrimSpace(req.Method+" "+req.Target),
		cs.reqIndex-1, w.bodyEnd, w.altEnd, w.overlap, viaSeen, w.note)

	s.tel.Emit(event{
		"type":      "signal",
		"signal":    w.signal,
		"synthetic": internal,
		"peer_ip":   cs.peer,
		"client_ip": cs.client,
		"attributes": map[string]any{
			"payload":    clip(w.reqLine, 1024),
			"detail":     clip(detail, 1024),
			"request_id": cs.id + "/" + strconv.Itoa(cs.reqIndex),
		},
	})
	log.Printf("counter %s conn=%s offset=%v via=%v", w.signal, cs.id, offsetProof, viaSeen)
}

// raise is the single funnel for the cache coherence counters.
func (s *server) raise(signal string, internal bool, payload, detail, requestID, peer, client string) {
	s.tel.Emit(event{
		"type":      "signal",
		"signal":    signal,
		"synthetic": internal,
		"peer_ip":   peer,
		"client_ip": client,
		"attributes": map[string]any{
			"payload":    clip(payload, 1024),
			"detail":     clip(detail, 1024),
			"request_id": requestID,
		},
	})
	log.Printf("counter %s %s", signal, clip(detail, 200))
}

// ---------------------------------------------------------------------------
// addresses
// ---------------------------------------------------------------------------

func parseCIDRs(spec string) []*net.IPNet {
	var out []*net.IPNet
	for _, part := range strings.Split(spec, ",") {
		part = strings.TrimSpace(part)
		if part == "" {
			continue
		}
		if _, n, err := net.ParseCIDR(part); err == nil {
			out = append(out, n)
		} else if ip := net.ParseIP(part); ip != nil {
			bits := 32
			if ip.To4() == nil {
				bits = 128
			}
			out = append(out, &net.IPNet{IP: ip, Mask: net.CIDRMask(bits, bits)})
		} else {
			log.Printf("config: ignoring unparsable address range %q", part)
		}
	}
	return out
}

func ipInAny(host string, nets []*net.IPNet) bool {
	ip := net.ParseIP(strings.TrimSpace(host))
	if ip == nil {
		return false
	}
	for _, n := range nets {
		if n.Contains(ip) {
			return true
		}
	}
	return false
}

func hostOf(addr string) string {
	if h, _, err := net.SplitHostPort(addr); err == nil {
		return h
	}
	return addr
}

func (s *server) attestedClient(cs *connState, req *wireRequest) string {
	if ipInAny(cs.peer, s.hopNets) {
		if v := strings.TrimSpace(req.Header("X-Real-IP")); v != "" {
			return v
		}
	}
	return cs.peer
}

// ---------------------------------------------------------------------------
// request records
// ---------------------------------------------------------------------------

func (s *server) emitRequest(cs *connState, req *wireRequest, status int) {
	route, _ := matchRoute(req)
	ev := event{
		"type":       "http_request",
		"method":     req.Method,
		"route":      route,
		"path":       clip(req.Target, 512),
		"status":     status,
		"peer_ip":    cs.peer,
		"client_ip":  cs.client,
		"synthetic":  cs.internal,
		"user_agent": req.Header("User-Agent"),
		"params":     collectParams(req),
	}
	if u := sessionUser(req.Header("Cookie")); u != "" {
		ev["auth_subject"] = u
	}
	s.tel.Emit(ev)
}

// collectParams records every input a handler here could read. On this tier that
// includes the routing headers, because at the edge the headers are the input.
func collectParams(req *wireRequest) []map[string]any {
	var out []map[string]any
	add := func(name, in, value string) {
		sum := sha256.Sum256([]byte(value))
		out = append(out, map[string]any{
			"name": name, "in": in,
			"value_sha256": hex.EncodeToString(sum[:]),
			"value_len":    len(value),
			"sample":       clip(value, 256),
		})
	}
	if i := strings.IndexByte(req.Target, '?'); i >= 0 {
		if q, err := url.ParseQuery(req.Target[i+1:]); err == nil {
			for name, vals := range q {
				if len(vals) > 0 {
					add(name, "query", vals[0])
				}
			}
		}
	}
	for _, h := range req.Headers {
		switch h.Canon {
		case "host", "x-forwarded-host", "x-forwarded-proto", "x-forwarded-scheme",
			"x-original-url", "x-rewrite-url", "transfer-encoding", "content-length":
			add(h.Raw, "header", h.Value)
		}
	}
	if len(req.Body) > 0 {
		add("<body>", "raw", string(req.Body))
	}
	return out
}

// ---------------------------------------------------------------------------
// responses
// ---------------------------------------------------------------------------

type response struct {
	status  int
	headers [][2]string
	body    []byte
}

var statusText = map[int]string{
	200: "OK", 201: "Created", 302: "Found", 400: "Bad Request", 401: "Unauthorized",
	404: "Not Found", 405: "Method Not Allowed", 500: "Internal Server Error",
}

func writeResponse(w *wireConn, req *wireRequest, r *response) error {
	var b strings.Builder
	txt := statusText[r.status]
	if txt == "" {
		txt = "OK"
	}
	fmt.Fprintf(&b, "HTTP/1.1 %d %s\r\n", r.status, txt)
	for _, h := range r.headers {
		b.WriteString(h[0])
		b.WriteString(": ")
		b.WriteString(h[1])
		b.WriteString("\r\n")
	}
	fmt.Fprintf(&b, "Content-Length: %d\r\n", len(r.body))
	b.WriteString("Connection: keep-alive\r\n")
	b.WriteString("Date: " + time.Now().UTC().Format(time.RFC1123) + "\r\n\r\n")
	if _, err := w.nc.Write([]byte(b.String())); err != nil {
		return err
	}
	if strings.EqualFold(req.Method, "HEAD") || len(r.body) == 0 {
		return nil
	}
	_, err := w.nc.Write(r.body)
	return err
}

func clip(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n]
}
