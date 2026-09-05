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

### Not yet done

- No target has been brought up under Docker. Every image build, database schema and
  container wiring in this release is unexecuted; the PHP and .NET code has never run
  at all.
- No tool has been benchmarked, so `results/` is empty and the comparison tables in the
  README are placeholders.
