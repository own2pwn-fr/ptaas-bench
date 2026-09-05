"""The estate's telemetry agent: lay the sites down, then report what happens to them.

One process, four readers and one listener:

* the readers turn what the web server and the datastores record about themselves into
  the estate's own records -- see :mod:`site_telemetry.httplog` and
  :mod:`site_telemetry.store_taps`;
* the listener runs the deployment routine on request and prints the digest of what it
  wrote, which is how an operator confirms two installations hold the same thing.

The listener is on the operations network only. Nothing on the customer-facing side can
reach it, and it is not a route on any of the sites: a site that could rebuild itself
over HTTP would be a site any visitor could empty.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import emit, evidence
from .config import settings
from .seed import run as deployment


def log(message: str) -> None:
    print(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {message}", file=sys.stderr, flush=True)


class Estate:
    def __init__(self) -> None:
        self.telemetry = emit.start(service=os.environ.get("TELEMETRY_SERVICE", "infra"))
        self.state = deployment.SeededState()
        self.counters = evidence.Counters(self.state, settings.sites_root, self.report,
                                          site_domain=settings.site_domain)
        self.access = None
        self.taps: list = []
        self.lock = threading.Lock()

    # -- reporting -----------------------------------------------------------

    def report(self, name: str, attributes: dict, *, peer: str = "",
               route: str | None = None, identifier: str | None = None) -> None:
        emit.signal(name, attributes, peer=peer, route=route, identifier=identifier)
        log(f"counter {name} peer={peer or '-'} {attributes.get('detail', '')}")

    # -- deployment ----------------------------------------------------------

    def deploy(self) -> str:
        with self.lock:
            state = deployment.deploy(settings)
            # The routine's own traffic -- loading the stores, measuring the listing --
            # is already in the logs. Everything written up to here belongs to the
            # deployment, so the counters start from this mark.
            time.sleep(0.3)
            try:
                floor = os.path.getsize(settings.access_log)
            except OSError:
                floor = 0
            self.state = state
            self.counters.reload(state, log_floor=floor, at=time.time())
            for tap in self.taps:
                tap.reset_budget()
            log(f"deployed state {state.digest} "
                f"listing={state.listing_bytes}{'' if state.listing_measured else ' (estimated)'} "
                f"empty-answer={state.search_empty_bytes}")
            return state.digest

    def deploy_with_retries(self, attempts: int = 30) -> None:
        # The web host has to be answering before the first deployment, not because the
        # files need it, but because the routine measures the generated listing through
        # it. Measured beats estimated: the estimate errs low by design, and a low mark is
        # a mark a smaller response could clear.
        deployment.wait_until_ready(settings)
        for attempt in range(1, attempts + 1):
            try:
                self.deploy()
                return
            except Exception as error:      # a store that is not up yet
                log(f"deployment attempt {attempt} did not complete: {error!r}")
                time.sleep(min(10.0, 2.0 * attempt))
        log("the estate could not be deployed; the readers are running anyway")

    # -- readers -------------------------------------------------------------

    def start_readers(self) -> None:
        from .httplog import AccessLog
        from .store_taps import KeyValueTap, RecordsTap, SearchTap

        self.access = AccessLog(settings.access_log, self.counters, settings.poll_interval,
                                site_domain=settings.site_domain)
        self.access.start()
        self.taps = [
            KeyValueTap("cache", settings.cache_host, settings.cache_port, self.counters,
                        route=settings.cache_label),
            KeyValueTap("queue", settings.queue_host, settings.queue_port, self.counters,
                        route=settings.queue_label),
            RecordsTap(settings.records_host, settings.records_port, settings.records_db,
                       self.counters, settings.poll_interval,
                       label=settings.records_label),
            SearchTap(settings.search_log_dir, self.counters, settings.poll_interval),
        ]
        for tap in self.taps:
            tap.start()

    def summary(self) -> str:
        from .store_taps import diagnostics

        seen = self.access.processed if self.access else 0
        return f"requests={seen} {diagnostics(self.taps)}"


class Listener(BaseHTTPRequestHandler):
    estate: Estate

    protocol_version = "HTTP/1.1"
    server_version = "operations/1.0"
    sys_version = ""

    def _reply(self, status: int, body: str) -> None:
        payload = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:                       # noqa: N802 - required spelling
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path == "/healthz":
            self._reply(200, "ok\n")
        elif path == "/status":
            self._reply(200, self.estate.summary() + "\n")
        elif path == "/deploy":
            self.do_POST()
        else:
            self._reply(404, "not found\n")

    def do_POST(self) -> None:                      # noqa: N802 - required spelling
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path != "/deploy":
            self._reply(404, "not found\n")
            return
        try:
            digest = self.estate.deploy()
        except Exception as error:                  # reported, never hidden
            log(f"deployment failed: {error!r}")
            self._reply(500, f"deployment failed: {error}\n")
            return
        self._reply(200, f"state {digest}\n")

    def log_message(self, fmt: str, *args) -> None:
        log("operations " + (fmt % args))


def main() -> int:
    estate = Estate()
    Listener.estate = estate
    estate.start_readers()
    threading.Thread(target=estate.deploy_with_retries, name="deploy",
                     daemon=True).start()

    host, _, port = settings.control_addr.rpartition(":")
    server = ThreadingHTTPServer((host or "0.0.0.0", int(port)), Listener)
    log(f"operations listener on {settings.control_addr}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
