# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

First public state. The platform and the corpus are complete and internally verified;
no benchmark run has been executed yet, and no baseline is published.

### Added

- **Corpus** — 170 planted vulnerabilities across eight targets, covering all 116
  classes of the taxonomy and every one of the thirty OWASP Top 10 category slots
  across the 2017, 2021 and 2025 editions. 805 declared routes, 632 of them safe, so
  precision has a denominator.
- **Targets** — a React SPA over Express with GraphQL and WebSockets, an Angular SPA
  over Spring Boot with an LDAP directory, a Vue SPA over FastAPI with MongoDB, a
  server-rendered PHP application, an ASP.NET Razor portal, a Flask and HTMX intranet,
  a chained nginx/HAProxy/Varnish proxy tier, and a static site with the artefacts a
  bad deployment leaves behind.
- **Scoring** — reach, exercise and trigger measured independently per vulnerability,
  aggregated by OWASP category for each edition, by family, class, severity, rendering
  mode, crawl difficulty and auth level. Three published precisions, and out-of-band
  attributions reported with their own confidence rather than flattened.
- **Instrumentation** — SDKs for Node, Python, PHP, Java and .NET, plus an access-log
  reader for the static target, all reporting the framework's route template rather
  than the concrete URL, and all off the request's critical path.
- **Egress sinkhole** — the resolver for the target network, capturing callbacks to
  whatever domain a tool chose, so blind vulnerabilities are measurable rather than
  unreachable.
- **Harness** — an orchestrator enforcing a recorded budget and a verified reset
  between runs, drivers for ZAP, nuclei, wapiti, nikto and skipfish, an import path for
  tools we ship no driver for, and a CWE mapping that emits null rather than guessing.
- **Deception** — targets built to be indistinguishable from ordinary applications,
  from the outside and after a compromise, so an agent that would change its behaviour
  on realising it is being evaluated has nothing to realise.

### Fixed — first bring-up

Seven of the eight targets have now been built, started and replayed against the
collector. 151 of the 170 entries fire their own counter exactly once, each carrying a
peer address, and every reset returns the same digest before and after a full replay.
What that first run turned up, beyond the per-target defects recorded in the commit
history, was three faults that would have corrupted a published comparison rather than
merely breaking a target:

- The proxy tier published the socket peer under a field name the collector does not
  read, so none of its signals could be placed at all.
- The proxy tier classified every framing signal as the platform's own traffic, because
  it read the classification off the smuggled request, which by construction has no
  attested client. All four smuggling entries would have been excluded from every
  tool's score, on every run, with nothing looking wrong.
- Three targets claimed the same operations name on the shared internal network, so the
  harness's own traffic could be sent to a different company's application than the one
  it was measuring.

### Known limitations

- `intranet` has not been brought up. Its image injects the observability package
  through a second build context, which requires BuildKit, and the build plugin is
  unavailable on the machine used for this bring-up. Nothing about the target is known
  to be wrong; it is simply unbuilt, and its 16 entries are unverified.
- **BENCH-EDGE-0003** (stream-downgrade desync) does not reproduce on the pinned stack
  and is left unfired rather than propped up. Neither hop capable of the downgrade will
  forward a declared length that contradicts the payload; the measurements are in the
  entry's notes. It is the only entry in the corpus known not to work.
- **BENCH-SHOP-0009 and -0010** are proved only by a real browser reporting a policy
  violation, so the replay cannot prove them from the container it runs in. The sinks
  are wired and correctly gated; a tool that drives a browser can score them.
- **BENCH-LEGY-0014** (field splitting in the attribution header) counts a value
  carrying a line break and a complete second field, but the server does not split on
  the wire: the interpreter hands the value on intact and the web server replaces the
  control bytes with spaces before the header block leaves. Confirmed by reading the
  emitted bytes, not the counter. As it stands the entry credits a payload rather than
  an effect, which is the failure this project exists to expose, and it needs either a
  hop that really splits or the entry withdrawn. It has deliberately not been adjusted
  in either direction here.
- No tool has been benchmarked, so `results/` is empty and the comparison tables in the
  README are placeholders.
