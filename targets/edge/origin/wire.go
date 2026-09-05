package main

// Hand-written HTTP/1.1 reader.
//
// WHY THIS IS NOT net/http (EDGE-441, EDGE-712)
// ---------------------------------------------
// Go's HTTP server is strict, and correctly so: it refuses a message carrying both
// Content-Length and Transfer-Encoding, refuses odd chunk sizes, and drops the
// connection when framing is ambiguous. That cost us two incidents. The load
// balancer in front of this tier runs the pre-HTX parser (kept because the payment
// vendor's callbacks and two partner integrations send coding headers HTX rejects
// outright), and it forwards those messages to us as it found them. net/http then
// answered 400 to traffic the LB had already accepted, and the failure surfaced as
// "random checkout errors" rather than as a framing problem.
//
// So this tier reads the wire itself and is tolerant in the same places the LB is:
//
//   1. A header name is everything before the first ':'; trailing space in the name
//      is accepted, and '_' is folded to '-' (a partner's SDK emits Transfer_Encoding
//      and their vendor will not ship a fix).
//   2. When several Transfer-Encoding headers arrive, the last one is used.
//   3. The value is stripped of OWS and of VT/FF/NUL before comparison; one upstream
//      prefixes it with a stray 0x0b.
//   4. Chunked applies only when the stripped value is exactly "chunked". A coding
//      list falls through to Content-Length, because when we honoured lists we
//      mis-read a partner's "identity, chunked" bodies.
//   5. Otherwise Content-Length, parsed leniently.
//
// CONNECTION INTEGRITY ACCOUNTING
// -------------------------------
// Being tolerant means we can end a message somewhere the LB did not, and then the
// keep-alive connection between us is off by however many bytes we disagreed about.
// That is not theoretical: it is what EDGE-712 was. So the reader tracks the absolute
// byte offset of everything it consumes, which lets frameCheck() below state the
// disagreement as a fact ("this request began 96 bytes before the previous body was
// supposed to end") instead of a suspicion. The counters it feeds are what page the
// on-call.

import (
	"bufio"
	"errors"
	"fmt"
	"io"
	"net"
	"strconv"
	"strings"
	"time"
)

const (
	maxHeaderLine  = 8 << 10
	maxHeaderCount = 100
	maxBodyBytes   = 1 << 20
	idleTimeout    = 30 * time.Second
)

// Anomaly counter names. Metric-shaped and stable: dashboards and alert rules key on
// these strings, so renaming one is a breaking change for the platform team.
const (
	sigUnderscoreCoding = "edge.http.framing.underscore_coding_mismatch"
	sigControlByte      = "edge.http.framing.control_byte_coding_mismatch"
	sigDuplicateCoding  = "edge.http.framing.duplicate_coding_mismatch"
	sigCodingList       = "edge.http.framing.coding_list_mismatch"
	sigUpgradedStream   = "edge.http.framing.upgraded_stream_length_mismatch"
)

var (
	errLineTooLong = errors.New("header line too long")
	errBadRequest  = errors.New("malformed request line")
	errTooManyHdrs = errors.New("too many headers")
)

// ---------------------------------------------------------------------------
// byte-accurate connection reader
// ---------------------------------------------------------------------------

type wireConn struct {
	nc       net.Conn
	br       *bufio.Reader
	consumed int64 // absolute offset of the next unconsumed byte on this connection
}

func newWireConn(nc net.Conn) *wireConn {
	return &wireConn{nc: nc, br: bufio.NewReaderSize(nc, maxHeaderLine)}
}

func (w *wireConn) readLine() (string, error) {
	b, err := w.br.ReadSlice('\n')
	w.consumed += int64(len(b))
	if err != nil {
		if errors.Is(err, bufio.ErrBufferFull) {
			return "", errLineTooLong
		}
		return "", err
	}
	return strings.TrimRight(string(b), "\r\n"), nil
}

func (w *wireConn) readN(n int) ([]byte, error) {
	if n <= 0 {
		return nil, nil
	}
	if n > maxBodyBytes {
		n = maxBodyBytes
	}
	buf := make([]byte, n)
	got, err := io.ReadFull(w.br, buf)
	w.consumed += int64(got)
	if err != nil {
		return buf[:got], err
	}
	return buf, nil
}

// ---------------------------------------------------------------------------
// headers and framing
// ---------------------------------------------------------------------------

type wireHeader struct {
	Raw   string // name exactly as received, whitespace and all
	Canon string // lowercased, '_' folded to '-' -- the comparison key (rule 1)
	Value string // OWS trimmed, control bytes preserved
}

type framing struct {
	Mode          string // "chunked" | "length" | "none"
	ContentLength int64
	HasCL         bool
	TERaw         []string
	TEChosen      string
	TEUnderscore  bool // a coding header arrived spelled with an underscore
	TECtl         bool // the chosen value carried VT/FF/NUL
	TEDuplicate   bool // more than one coding header
	TEList        bool // the chosen value is a comma list
	Reason        string
}

func foldName(s string) string {
	return strings.ToLower(strings.ReplaceAll(strings.TrimSpace(s), "_", "-"))
}

// stripWeird removes OWS plus the control bytes some upstreams prefix (rule 3). The
// second result records that a control byte had to be removed for the value to
// match, which is what tells the two counters apart downstream.
func stripWeird(s string) (string, bool) {
	return strings.Trim(s, " \t\v\f\x00"), strings.ContainsAny(s, "\v\f\x00")
}

func decideFraming(hdrs []wireHeader) framing {
	f := framing{Mode: "none"}
	for _, h := range hdrs {
		switch h.Canon {
		case "transfer-encoding":
			f.TERaw = append(f.TERaw, h.Value)
			if strings.Contains(h.Raw, "_") {
				f.TEUnderscore = true
			}
		case "content-length":
			if !f.HasCL {
				v := strings.TrimPrefix(strings.TrimSpace(h.Value), "+")
				if n, err := strconv.ParseInt(strings.TrimSpace(v), 10, 64); err == nil && n >= 0 {
					f.ContentLength = n
					f.HasCL = true
				}
			}
		}
	}
	f.TEDuplicate = len(f.TERaw) > 1
	if len(f.TERaw) > 0 {
		raw := f.TERaw[len(f.TERaw)-1] // rule 2: last one wins
		stripped, ctl := stripWeird(raw)
		f.TEChosen, f.TECtl = stripped, ctl
		f.TEList = strings.Contains(stripped, ",")
		if strings.EqualFold(stripped, "chunked") {
			f.Mode = "chunked"
			f.Reason = "last coding header is chunked after stripping"
			return f
		}
	}
	if f.HasCL {
		f.Mode = "length"
		if len(f.TERaw) > 0 {
			f.Reason = "coding header present but unrecognised; using Content-Length"
		} else {
			f.Reason = "Content-Length"
		}
		return f
	}
	f.Reason = "no framing headers"
	return f
}

// ---------------------------------------------------------------------------
// request
// ---------------------------------------------------------------------------

type wireRequest struct {
	Method  string
	Target  string
	Proto   string
	Headers []wireHeader
	Body    []byte

	StartOffset int64 // absolute offset of the first byte of the request line
	BodyStart   int64
	BodyEnd     int64 // absolute offset after the body THIS reader consumed
	RawBody     []byte

	Framing framing
}

func (r *wireRequest) Header(name string) string {
	want := foldName(name)
	for _, h := range r.Headers {
		if h.Canon == want {
			return h.Value
		}
	}
	return ""
}

func (r *wireRequest) HasHeader(name string) bool {
	want := foldName(name)
	for _, h := range r.Headers {
		if h.Canon == want {
			return true
		}
	}
	return false
}

// lastHopProto returns the received-protocol token of the final Via entry, i.e. the
// version spoken by the hop that handed us this request ("1.1", "2.0"), or "" when
// no Via is present at all. RFC 9110 has each intermediary append its own entry, so
// the last one is always the nearest hop.
func (r *wireRequest) lastHopProto() string {
	last := ""
	for _, h := range r.Headers {
		if h.Canon != "via" {
			continue
		}
		for _, part := range strings.Split(h.Value, ",") {
			if p := strings.TrimSpace(part); p != "" {
				last = p
			}
		}
	}
	if last == "" {
		return ""
	}
	tok := strings.Fields(last)[0]
	return strings.TrimPrefix(tok, "HTTP/")
}

func readRequest(w *wireConn) (*wireRequest, error) {
	_ = w.nc.SetReadDeadline(time.Now().Add(idleTimeout))
	start := w.consumed
	line, err := w.readLine()
	if err != nil {
		return nil, err
	}
	// Leading empty lines are tolerated, as RFC 9112 recommends.
	for line == "" {
		start = w.consumed
		if line, err = w.readLine(); err != nil {
			return nil, err
		}
	}
	parts := strings.SplitN(line, " ", 3)
	if len(parts) < 2 {
		return nil, errBadRequest
	}
	req := &wireRequest{Method: parts[0], Target: parts[1], StartOffset: start, Proto: "HTTP/1.1"}
	if len(parts) == 3 {
		req.Proto = parts[2]
	}
	for {
		h, err := w.readLine()
		if err != nil {
			return nil, err
		}
		if h == "" {
			break
		}
		if len(req.Headers) >= maxHeaderCount {
			return nil, errTooManyHdrs
		}
		idx := strings.IndexByte(h, ':')
		if idx < 0 {
			continue // junk line: drop it rather than kill the connection
		}
		// Trim only real OWS (SP and HTAB). strings.TrimSpace would also eat VT and
		// FF, which rule 3 needs to see.
		req.Headers = append(req.Headers, wireHeader{
			Raw:   h[:idx],
			Canon: foldName(h[:idx]),
			Value: strings.Trim(h[idx+1:], " \t"),
		})
	}
	req.Framing = decideFraming(req.Headers)
	req.BodyStart = w.consumed

	switch req.Framing.Mode {
	case "chunked":
		body, err := readChunked(w)
		if err != nil {
			return nil, err
		}
		req.Body = body
	case "length":
		raw, err := w.readN(int(req.Framing.ContentLength))
		if err != nil {
			return nil, err
		}
		req.Body, req.RawBody = raw, raw
	}
	req.BodyEnd = w.consumed
	return req, nil
}

func readChunked(w *wireConn) ([]byte, error) {
	var out []byte
	for {
		sizeLine, err := w.readLine()
		if err != nil {
			return out, err
		}
		if i := strings.IndexByte(sizeLine, ';'); i >= 0 {
			sizeLine = sizeLine[:i] // chunk extensions ignored
		}
		sizeLine = strings.TrimSpace(sizeLine)
		if sizeLine == "" {
			continue
		}
		n, err := strconv.ParseInt(sizeLine, 16, 64)
		if err != nil || n < 0 {
			return out, fmt.Errorf("bad chunk size %q", sizeLine)
		}
		if n == 0 {
			for { // trailer section
				t, err := w.readLine()
				if err != nil {
					return out, err
				}
				if t == "" {
					return out, nil
				}
			}
		}
		chunk, err := w.readN(int(n))
		if err != nil {
			return out, err
		}
		out = append(out, chunk...)
		if len(out) > maxBodyBytes {
			return out, errors.New("body too large")
		}
		if _, err := w.readLine(); err != nil { // chunk's trailing CRLF
			return out, err
		}
	}
}

// ---------------------------------------------------------------------------
// connection integrity accounting
// ---------------------------------------------------------------------------

// frameWatch is what one request leaves behind for the next one to be checked
// against. It exists only when the message declared two framings that disagree, or
// when it arrived from a hop that had just converted it down from HTTP/2.
type frameWatch struct {
	signal   string
	kind     string // "cl-te" | "te-cl" | "h2-cl"
	reqLine  string
	bodyEnd  int64 // where this reader ended the body
	altEnd   int64 // where the other reading would have ended it (0 = not computable)
	overlap  int64
	note     string
	internal bool // the request that armed this came from our own address range
}

// firstChunkEnd computes where a chunked reader would have put the end of the first
// chunk, given the bytes this reader consumed as a Content-Length body. It is how the
// second case below states its disagreement as an offset rather than a hunch.
func firstChunkEnd(bodyStart int64, raw []byte) (int64, bool) {
	i := 0
	for i < len(raw) && (raw[i] == '\r' || raw[i] == '\n') {
		i++
	}
	j := i
	for j < len(raw) && raw[j] != '\r' && raw[j] != '\n' {
		j++
	}
	if j >= len(raw) {
		return 0, false
	}
	tok := strings.TrimSpace(string(raw[i:j]))
	if k := strings.IndexByte(tok, ';'); k >= 0 {
		tok = tok[:k]
	}
	n, err := strconv.ParseInt(strings.TrimSpace(tok), 16, 64)
	if err != nil || n <= 0 {
		return 0, false
	}
	crlf := int64(2)
	if raw[j] == '\n' {
		crlf = 1
	}
	return bodyStart + int64(i) + int64(j-i) + crlf + n + 2, true
}

// frameCheck decides whether a request we have just finished reading could have left
// the connection off by some number of bytes, and which counter that would be.
// Attribution is on what was observed on the wire, never on anything the sender
// merely asserts.
func frameCheck(req *wireRequest, hopProto string, internal bool) *frameWatch {
	f := req.Framing
	line := req.Method + " " + req.Target + " " + req.Proto

	switch {
	case f.Mode == "chunked" && f.HasCL:
		// We ended the body at the zero chunk; a hop reading Content-Length believes
		// it runs to BodyStart+CL. Everything past our end is still body to them.
		altEnd := req.BodyStart + f.ContentLength
		if altEnd <= req.BodyEnd {
			return nil
		}
		w := &frameWatch{
			internal: internal, kind: "cl-te", reqLine: line,
			bodyEnd: req.BodyEnd, altEnd: altEnd, overlap: altEnd - req.BodyEnd,
		}
		switch {
		case f.TEUnderscore:
			w.signal = sigUnderscoreCoding
			w.note = "coding header spelled with an underscore: opaque to the hop in front, authoritative here"
		case f.TECtl:
			w.signal = sigControlByte
			w.note = "coding value carried control bytes the hop in front did not strip"
		default:
			return nil // ambiguous; counted as a note, never as an anomaly
		}
		return w

	case len(f.TERaw) > 0 && f.Mode != "chunked":
		// We did not read chunked although a coding header was present, so the hop in
		// front may well have. Two shapes arrive here: "length" (it also sent a
		// Content-Length, which we used) and "none" (the hop consumed the
		// Content-Length itself once it decided the message was chunked, leaving us
		// with no framing and a zero-length body).
		//
		// The offset is computable only in the first shape, where we still hold the
		// bytes we read. In the second the watch is still armed and check() falls
		// back to the Via test, which no genuinely forwarded request can fail.
		w := &frameWatch{internal: internal, kind: "te-cl", reqLine: line, bodyEnd: req.BodyEnd}
		if altEnd, ok := firstChunkEnd(req.BodyStart, req.RawBody); ok && altEnd > req.BodyEnd {
			w.altEnd, w.overlap = altEnd, altEnd-req.BodyEnd
		}
		switch {
		case f.TEDuplicate:
			w.signal = sigDuplicateCoding
			w.note = "two coding headers; the hop in front read the first, we read the last"
		case f.TEList:
			w.signal = sigCodingList
			w.note = "coding list ends in chunked for the hop in front but is not literally chunked here"
		default:
			return nil
		}
		return w

	case f.Mode == "length" && len(f.TERaw) == 0 && hopProto == "2.0":
		// The hop in front converted an HTTP/2 stream down to HTTP/1.1 using the
		// length the client declared rather than the payload it forwarded. There is no
		// second framing header to compare against, so no offset is computable; the
		// Via test in check() carries this one.
		return &frameWatch{
			internal: internal, signal: sigUpgradedStream, kind: "h2-cl",
			reqLine: line, bodyEnd: req.BodyEnd,
			note:    "stream converted down from 2.0 with a client-declared length",
		}
	}
	return nil
}
