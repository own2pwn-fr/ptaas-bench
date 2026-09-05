package main

// Internal observability client.
//
// Metrics and anomaly counters are shipped to the platform team's OTLP-ish endpoint
// (TELEMETRY_ENDPOINT). Three rules, learned the hard way during the 2024 checkout
// outage, when a slow metrics backend became a slow storefront:
//
//   * Emit() never blocks. It does a non-blocking send on a bounded channel and
//     returns. If the channel is full the record is discarded and counted.
//   * Emit() never fails. An endpoint that is down, slow or absent is
//     indistinguishable from one that accepted everything.
//   * One background goroutine drains the channel and posts batches, so ordering is
//     stable and this process has at most one request in flight.
//
// Anything that changed these rules would put the observability stack on the
// critical path of every request this tier serves.

import (
	"bytes"
	"context"
	"encoding/json"
	"log"
	"net/http"
	"os"
	"strings"
	"sync"
	"time"
)

const (
	queueSize  = 8192
	batchMax   = 500
	flushEvery = 200 * time.Millisecond
)

type event map[string]any

type telemetry struct {
	endpoint string
	service  string
	ch       chan event
	http     *http.Client
	wg       sync.WaitGroup
	closed   chan struct{}

	mu      sync.Mutex
	dropped int64
}

func newTelemetry() *telemetry {
	t := &telemetry{
		endpoint: strings.TrimRight(envOr("TELEMETRY_ENDPOINT", "http://otel-collector:8900"), "/"),
		service:  envOr("TELEMETRY_SERVICE", "edge"),
		ch:       make(chan event, queueSize),
		// Short on purpose: a hung endpoint must not pin the sender goroutine for
		// minutes while the queue fills up silently behind it.
		http:   &http.Client{Timeout: 3 * time.Second},
		closed: make(chan struct{}),
	}
	t.wg.Add(1)
	go t.loop()
	return t
}

// Emit stamps the shared fields and enqueues. Never blocks, never errors.
func (t *telemetry) Emit(ev event) {
	if t == nil || ev == nil {
		return
	}
	ev["app"] = t.service
	if _, ok := ev["ts"]; !ok {
		ev["ts"] = float64(time.Now().UnixNano()) / 1e9
	}
	select {
	case t.ch <- ev:
	default:
		t.mu.Lock()
		t.dropped++
		n := t.dropped
		t.mu.Unlock()
		if n == 1 || n%1000 == 0 {
			log.Printf("telemetry: queue full, discarded %d records", n)
		}
	}
}

func (t *telemetry) loop() {
	defer t.wg.Done()
	ticker := time.NewTicker(flushEvery)
	defer ticker.Stop()
	batch := make([]event, 0, batchMax)
	for {
		select {
		case ev := <-t.ch:
			batch = append(batch, ev)
			if len(batch) >= batchMax {
				t.post(batch)
				batch = batch[:0]
			}
		case <-ticker.C:
			if len(batch) > 0 {
				t.post(batch)
				batch = batch[:0]
			}
		case <-t.closed:
			for drained := true; drained; {
				drained = false
				select {
				case ev := <-t.ch:
					batch = append(batch, ev)
					drained = true
					if len(batch) >= batchMax {
						t.post(batch)
						batch = batch[:0]
					}
				default:
				}
			}
			if len(batch) > 0 {
				t.post(batch)
			}
			return
		}
	}
}

func (t *telemetry) post(batch []event) {
	body, err := json.Marshal(map[string]any{"events": batch})
	if err != nil {
		log.Printf("telemetry: marshal failed: %v", err)
		return
	}
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, t.endpoint+"/v1/traces", bytes.NewReader(body))
	if err != nil {
		return
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := t.http.Do(req)
	if err != nil {
		return // expected while the endpoint restarts; not fatal, by design
	}
	_ = resp.Body.Close()
}

// Close flushes best-effort. Shutdown only; the request path never calls it.
func (t *telemetry) Close() {
	close(t.closed)
	done := make(chan struct{})
	go func() { t.wg.Wait(); close(done) }()
	select {
	case <-done:
	case <-time.After(2 * time.Second):
	}
}

func envOr(name, def string) string {
	if v := os.Getenv(name); v != "" {
		return v
	}
	return def
}
