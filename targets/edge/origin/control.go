package main

// Loopback-only operations listener.
//
// Bound to 127.0.0.1 inside this container, so it is reachable from a shell on this
// container and from nothing on the network — not from the tier in front, not from
// the cache, not from anywhere a customer's packets can go. That is why the ops
// tooling talks to it instead of exposing a route on the public listener, where any
// customer could have driven it.
//
// /reset drops in-memory state (sessions, and the coherence probe's short-term
// de-duplication window) and asks the cache tier to invalidate everything, so that a
// fresh deployment and a recycled one behave identically. It answers with a one-line
// digest of the state that is supposed to be constant: run it twice and the digest
// must be the same, and if it is not, something is still holding state.

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"log"
	"net"
	"net/http"
	"sort"
	"strconv"
	"time"
)

func (s *server) serveControl() {
	addr := envOr("CONTROL_ADDR", "127.0.0.1:9901")
	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) {
		fmt.Fprintln(w, "ok")
	})
	mux.HandleFunc("/reset", func(w http.ResponseWriter, r *http.Request) {
		dropped := clearSessions()
		s.probe.reset()
		cacheErr := s.invalidateCache()
		if cacheErr != nil {
			// Non-fatal on its own, but the operator has to know: a surviving cached
			// object is state that leaks from one run into the next.
			log.Printf("reset: cache invalidation failed: %v", cacheErr)
			w.WriteHeader(http.StatusInternalServerError)
			fmt.Fprintf(w, "cache invalidation failed: %v\n", cacheErr)
			return
		}
		log.Printf("reset: %d sessions dropped, cache invalidated", dropped)
		fmt.Fprintln(w, s.stateDigest())
	})
	srv := &http.Server{
		Addr:              addr,
		Handler:           mux,
		ReadHeaderTimeout: 5 * time.Second,
	}
	log.Printf("control listener on %s", addr)
	if err := srv.ListenAndServe(); err != nil {
		log.Printf("control listener stopped: %v", err)
	}
}

// stateDigest summarises everything that must be identical between two freshly reset
// deployments: the seeded identity, the seeded accounts and their tokens, and the
// count of anything held in memory (which must be zero).
func (s *server) stateDigest() string {
	h := sha256.New()
	fmt.Fprintf(h, "site|%s|%s|%s\n", s.site.Name, s.site.Domain, s.site.CanonicalHost)
	accountsMu.RLock()
	logins := append([]string(nil), accountOrder...)
	sort.Strings(logins)
	for _, login := range logins {
		if a := accountsBy[login]; a != nil {
			fmt.Fprintf(h, "account|%s|%s|%s\n", a.Login, a.Display, a.Token)
		}
	}
	accountsMu.RUnlock()
	fmt.Fprintf(h, "sessions|%d\npending|%d\n", sessionCount(), s.probe.pending())
	return "state " + hex.EncodeToString(h.Sum(nil))[:32]
}

// invalidateCache asks the cache tier to drop everything. It is a plain HTTP request
// on the internal network rather than an admin socket because the cache runs in its
// own container and the admin socket is not shared; the cache config only honours the
// method from inside the estate.
func (s *server) invalidateCache() error {
	addr := envOr("CACHE_ADMIN_ADDR", "varnish:80")
	conn, err := net.DialTimeout("tcp", addr, 3*time.Second)
	if err != nil {
		return err
	}
	defer conn.Close()
	_ = conn.SetDeadline(time.Now().Add(5 * time.Second))
	req := "BAN / HTTP/1.1\r\nHost: " + s.site.CanonicalHost + "\r\nConnection: close\r\n\r\n"
	if _, err := conn.Write([]byte(req)); err != nil {
		return err
	}
	buf := make([]byte, 256)
	n, err := conn.Read(buf)
	if err != nil && n == 0 {
		return err
	}
	line := string(buf[:n])
	if len(line) < 12 || line[:5] != "HTTP/" {
		return fmt.Errorf("unexpected reply from cache tier: %q", clip(line, 80))
	}
	code, convErr := strconv.Atoi(line[9:12])
	if convErr != nil || code < 200 || code > 299 {
		return fmt.Errorf("cache tier answered %q", clip(line, 40))
	}
	return nil
}
