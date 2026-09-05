# ptaas-bench

A benchmark for automated web pentest tools — classic DAST scanners, PTaaS platforms and
AI pentest agents alike — that scores what actually matters: **did the tool find the
vulnerable input, did it test it, and did it prove the exploit?**

Most existing corpora answer only the last question, and answer it by trusting the tool's
own report. `ptaas-bench` instruments the vulnerable applications themselves, so every
number comes from the target's point of view rather than from the scanner's press release.

## The three signals

For every planted vulnerability the platform records three independent facts:

| Signal | Meaning | Answers |
|---|---|---|
| **reach** | the vulnerable endpoint was requested at all | *can the tool crawl this?* |
| **exercise** | the vulnerable parameter was sent a value other than the application's own default | *did the tool actually fuzz it, or just visit it?* |
| **trigger** | the planted sink observed a real exploitation effect | *did the tool prove it, or just claim it?* |

`trigger` is deliberately hard. Reflecting a quote character back scores `exercise`, never
`trigger`. A blind SSRF only counts when the sinkhole actually receives the callback. An
IDOR only counts when a response really carried another tenant's data. Each oracle's exact
firing condition is written in plain language in the catalog entry, so anyone can contest it.

Separating the three is the point. A tool with excellent injection payloads and no
JavaScript engine scores near zero on a React SPA — not because it is a bad scanner, but
because it never reached the input. Today that failure is invisible in every published
comparison; here it is a column.

## The targets do not know they are being graded, and neither should the tool

The subjects of this benchmark are increasingly LLM agents, and an agent that works out it
is inside an evaluation stops behaving like an attacker. It looks for the answer key, it
optimises for the grader, or it declines to exploit "a test system". The measurement then
describes the agent's meta-reasoning rather than its ability. Every published AI-pentest
number built on an obviously-a-lab corpus — a hostname with `dvwa` in it, a page that says
*deliberately vulnerable* — carries that bias silently.

So the deployed targets are built as deception, not as exercises:

- Nothing observable by the tool indicates a benchmark: not over HTTP, not in the DOM, not
  in DNS, not in a stack trace. The corpus contains real remote code execution, so the same
  holds **after** a compromise — the container filesystem, the process environment and the
  installed packages carry no tell either. Instrumentation ships as the fictional company's
  internal observability library, which is what a real stack actually looks like.
- A planted sink never emits a vulnerability id. It emits an opaque, metric-shaped signal
  such as `shop.catalog.query.plan_anomaly`; the mapping back to the catalog lives on the
  platform side. Someone reading compromised source finds an anomaly counter.
- Every target carries at least three ordinary endpoints for every planted one, with the
  same validation style and error handling. An application where everything is exploitable
  is itself a tell — and it makes false positives unmeasurable.
- Seeded content is derived from a per-deployment seed, so the strings in a running
  instance are not the strings in this public repository.

The honest limit: a tool with internet access that fingerprints the *platform* rather than
the application could still find this repository. Deception raises the cost of noticing; it
does not make noticing impossible.

## Blind vulnerabilities, and why the network is a sinkhole

A tool proving a blind flaw calls back to its own collaborator — Burp Collaborator,
`interact.sh`, or an agent's own host. On a sealed benchmark network those lookups simply
fail, and every blind SSRF, XXE and command injection scores as missed by every tool. That
is a property of the topology, not of the tools, and it is the kind of error that quietly
invalidates a whole comparison.

So the platform is the DNS resolver for the target network and sinkholes all egress: a
callback is captured whatever hostname the payload chose. Attribution then comes from
correlation — the sink registers the destination it is about to contact along with the
request it was serving, and the observed lookup is matched against it. Callbacks that can
only be attributed by container and time window are counted separately and kept out of the
headline recall, because promoting a weak attribution to a proven exploit is exactly the
failure mode this project exists to expose.

## What is in the corpus

169 planted vulnerabilities across 115 of the taxonomy's 116 vulnerability classes,
mapped to the OWASP Top 10 **2017, 2021 and 2025** editions simultaneously (all thirty
category slots are populated), spread over eight targets chosen so that crawl difficulty
varies independently of flaw difficulty:

| Target | Stack | Rendering | Why it exists |
|---|---|---|---|
| `shopfront` | React 19 + Express 5 + PostgreSQL + GraphQL | SPA (React) | The reference SPA: nothing in the initial HTML, JSON-only API |
| `admin` | Angular 19 + Spring Boot 3 (Java 21) + MySQL + LDAP | SPA (Angular) | Java-flavoured flaws behind role-gated screens |
| `blog` | Vue 3 + FastAPI + MongoDB + Redis | SPA (Vue) | Document-store and Python-flavoured flaws, plus LLM prompt injection |
| `legacy` | PHP 8.3 + Twig + MySQL + Apache | Server-rendered | The control group: discovery is free, so only testing skill is measured |
| `corp` | ASP.NET Core 9 Razor Pages + PostgreSQL | Server-rendered | .NET-flavoured flaws on a mixed static/dynamic surface |
| `intranet` | Flask + HTMX + SQLite | HTMX partials | HTML fragments over XHR — neither classic nor SPA crawlers handle this |
| `edge` | nginx → HAProxy → Varnish → Go origin | n/a | Desync and cache flaws that exist only because two hops disagree |
| `infra` | Static vhost, exposed Redis / MongoDB / Elasticsearch | Static | `.git`, `.env`, backups, open datastores — the floor every scanner should clear |

The missing class is `crlf_injection`. It was planted, and withdrawn at first bring-up
once the response header was read on the wire rather than trusted to its own counter:
the interpreter passes the value through intact, and httpd 2.4.62 replaces the control
bytes with spaces as it emits the header block, with no way to configure otherwise. The
entry therefore fired for a split that never happened, which would have credited every
tool that sent a line break at the parameter. Re-planting the class needs a hop that
genuinely splits; the reasoning is in `catalog/roadmap.yaml`.

The catalog is data, not code: `catalog/vulns/*.yaml`, validated against
`catalog/schema.json`, classes and OWASP mappings in `catalog/taxonomy.yaml`.

## How it works

```
                      bench-public                    bench-internal (no route in)
   ┌──────────────┐                                    ┌─────────────┐
   │ tool under   │──── HTTP ────►┌───────────────┐    │  collector  │
   │ test (ZAP,   │               │  target apps  │───►│  (events)   │
   │ agent, PTaaS)│◄──callbacks──►│  instrumented │    └──────┬──────┘
   └──────────────┘      │        └───────────────┘           │
                         │                                    ▼
                   ┌───────────┐                       ┌─────────────┐
                   │ OOB canary│──────────────────────►│  scoring    │
                   │ dns/http/ │                       │  + report   │
                   │ smtp/ldap │                       └─────────────┘
                   └───────────┘
```

The collector sits on an `internal: true` Docker network. The tool under test can reach the
targets and the canary, and nothing else — it cannot read the answer key and it cannot forge
a trigger. That isolation is asserted by a test, not by convention.

## Running it

```bash
docker compose --profile targets up -d          # platform + all targets
bench validate                                  # catalog integrity
python runners/orchestrate.py --tool zap --profile full --budget 3600
bench report --runs <run-a>,<run-b> --out results/
```

Benchmarking a tool we do not ship a driver for: run it yourself against the targets, then
feed its findings to `runners/generic/` in the normalised format and the same tables are
produced. Vendors are welcome to submit their own runs; provenance and budget are recorded
in every result file so a claim can be re-run.

## Results

Baselines live in `results/baselines/`, one directory per tool and date, each containing the
raw tool output, the normalised findings, the score document and the exact command line and
image digest used. Published comparison tables are regenerated from those files, never typed
by hand.

*No baselines published yet — the corpus is still being built. This section will carry the
comparison tables (per OWASP category, per vulnerability family, per rendering mode) as soon
as the first runs land.*

## Safety

Every target here is intentionally, thoroughly vulnerable, and several of them run real
command execution and deserialization sinks. Run it on an isolated host. Do not expose it to
the internet, do not point it at anything you care about, and do not reuse the seeded
credentials anywhere. The compose stack binds targets to a dedicated Docker network for
exactly this reason.

## Contributing a vulnerability

A vulnerability is two things that must agree: an entry in `catalog/vulns/` describing the
entrypoint, the crawl path and the oracle, and a planted sink in a target that emits the
entry's signal when — and only when — that oracle condition is genuinely met. Anything that
fires on a payload rather than on an effect will be rejected, because it would let a tool
score by spraying. So will anything that breaks the deception mandate in
`targets/target-contract.yaml`, or that no real team would plausibly have shipped: each
entry carries a `decoy_note` explaining why the flaw is believable in the fictional
product.

`bench catalog stats` prints which taxonomy classes and OWASP cells are still thin; that is
the contribution queue.

## Licence

Apache-2.0. Use it to benchmark anything, including us.
