"""Wires the five listeners, the store, the reporting client, the correlation index and
the admin API into one process on one event loop.

Two ways to run it, sharing all of the setup:

* ``ResolverService(config).run()`` -- blocking, used by ``python -m edge_resolver``;
* ``start()`` / ``stop()`` -- spins the loop in a background thread, which is how the
  tests drive it with ordinary blocking sockets.

A listener that fails to bind is fatal, except HTTPS: if the TLS material cannot be
generated we log it and carry on, because four working channels beat none.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import socket
import threading

from .config import Config
from .correlation import AttributionWorker, CorrelationIndex, HintPoller
from .listeners.admin import AdminHandler
from .listeners.dns import DnsHandler, DnsUdpProtocol, handle_tcp
from .listeners.ldap import LdapHandler
from .listeners.smtp import SmtpHandler
from .listeners.web import WebHandler
from .net import local_address_towards
from .recorder import Recorder
from .store import ObservationStore
from .telemetry import TelemetryClient

log = logging.getLogger("edge_resolver.service")


class ResolverService:
    def __init__(self, config: Config, upstream=None) -> None:
        self.config = config
        # Optional override for the upstream resolver used to answer internal names.
        # Production leaves it None and goes through the container's own resolver.
        self.upstream = upstream
        self.store = ObservationStore(maxlen=config.store_size)
        self.telemetry = TelemetryClient(
            config.telemetry_url,
            queue_size=config.queue_size,
            batch_size=config.batch_size,
            flush_interval=config.flush_interval,
            timeout=config.request_timeout,
        )
        self.index = CorrelationIndex(hint_ttl=config.hint_ttl, source_ttl=config.source_ttl)
        self.index.set_static_sources(config.app_sources)
        # Slow full listing: its only job is keeping the address-to-application map warm.
        self.poller = HintPoller(
            self.index, self.telemetry.fetch_hints, interval=config.hint_poll_interval
        )
        self.recorder = Recorder(config, self.store, self.telemetry, self.index)
        # Targeted lookups, off the listener path. The recorder learns about the worker
        # after construction because the worker reports back through the recorder.
        self.worker: AttributionWorker | None = None
        if config.lookup_enabled:
            self.worker = AttributionWorker(
                self.index,
                self.telemetry.fetch_hints,
                self.recorder.finalise,
                queue_size=config.lookup_queue_size,
            )
            self.recorder.worker = self.worker
        self.certificates = None
        self.ports: dict[str, int] = {}
        self.tls_error: str | None = None

        self._servers: list[asyncio.AbstractServer] = []
        self._udp_transport: asyncio.DatagramTransport | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event: asyncio.Event | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._error: BaseException | None = None

    # -- lifecycle --------------------------------------------------------------

    def run(self) -> None:
        asyncio.run(self._amain(install_signals=True))

    def start(self, timeout: float = 20.0) -> "ResolverService":
        if self._thread is not None:
            return self
        self._ready.clear()
        self._thread = threading.Thread(
            target=lambda: asyncio.run(self._amain(install_signals=False)),
            name="resolver",
            daemon=True,
        )
        self._thread.start()
        if not self._ready.wait(timeout):
            raise RuntimeError("service did not start in time")
        if self._error is not None:
            raise RuntimeError(f"service failed to start: {self._error}")
        return self

    def stop(self, timeout: float = 10.0) -> None:
        loop, event = self._loop, self._stop_event
        if loop is not None and event is not None and not loop.is_closed():
            loop.call_soon_threadsafe(event.set)
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
        self._thread = None

    def __enter__(self) -> "ResolverService":
        return self.start()

    def __exit__(self, *exc_info) -> None:
        self.stop()

    # -- internals --------------------------------------------------------------

    async def _amain(self, install_signals: bool) -> None:
        self._loop = asyncio.get_running_loop()
        self._stop_event = asyncio.Event()
        # A client speaking plain HTTP to :443 is ordinary traffic here, not a bug; keep
        # those handshake errors out of stderr.
        self._loop.set_exception_handler(_quiet_exception_handler)
        if install_signals:
            for sig in (signal.SIGINT, signal.SIGTERM):
                with contextlib.suppress(NotImplementedError):
                    self._loop.add_signal_handler(sig, self._stop_event.set)
        self.telemetry.start()
        self.poller.start()
        if self.worker is not None:
            self.worker.start()
        try:
            await self._setup()
        except BaseException as exc:  # noqa: BLE001 - reported to the starting thread
            self._error = exc
            self._ready.set()
            await self._teardown()
            raise
        self._ready.set()
        log.info("listening for %s: %s", self.config.zone, self.ports)
        try:
            await self._stop_event.wait()
        finally:
            await self._teardown()

    async def _setup(self) -> None:
        cfg = self.config
        loop = asyncio.get_running_loop()
        dns_handler = DnsHandler(cfg, self.recorder, upstream=self.upstream)

        transport, _ = await loop.create_datagram_endpoint(
            lambda: DnsUdpProtocol(dns_handler),
            local_addr=(cfg.listen_host, cfg.dns_udp_port),
        )
        self._udp_transport = transport
        self.ports["dns_udp"] = transport.get_extra_info("socket").getsockname()[1]

        self.ports["dns_tcp"] = await self._serve(
            lambda r, w: handle_tcp(dns_handler, r, w), cfg.listen_host, cfg.dns_tcp_port
        )
        self.ports["http"] = await self._serve(
            WebHandler(cfg, self.recorder, "http"), cfg.listen_host, cfg.http_port
        )
        self.ports["smtp"] = await self._serve(
            SmtpHandler(cfg, self.recorder), cfg.listen_host, cfg.smtp_port
        )
        self.ports["ldap"] = await self._serve(
            LdapHandler(cfg, self.recorder), cfg.listen_host, cfg.ldap_port
        )
        admin_host = self._admin_host()
        # Allowlist additions we can prove belong to the platform: the reporting
        # endpoint's own address, and our address on the same network (the health probe
        # and the orchestrator reach us through one of the two).
        extra = [f"{address}/32" for address in self._platform_addresses()]
        self.ports["admin"] = await self._serve(
            AdminHandler(
                cfg,
                self.store,
                self.telemetry,
                self.index,
                self.poller,
                self.worker,
                cfg.effective_admin_networks(extra),
            ),
            admin_host,
            cfg.admin_port,
        )

        try:
            from .certs import CertificateFactory

            self.certificates = await loop.run_in_executor(
                None,
                CertificateFactory,
                cfg.ca_name,
                cfg.default_certificate_name,
            )
            self.ports["https"] = await self._serve(
                WebHandler(cfg, self.recorder, "https", self.certificates),
                cfg.listen_host,
                cfg.https_port,
                ssl=self.certificates.server_context(),
            )
        except Exception as exc:  # noqa: BLE001 - TLS is the one optional channel
            self.tls_error = f"{type(exc).__name__}: {exc}"
            log.error("TLS listener disabled: %s", self.tls_error)

    async def _serve(self, handler, host: str, port: int, **kwargs) -> int:
        server = await asyncio.start_server(handler, host, port, **kwargs)
        self._servers.append(server)
        return server.sockets[0].getsockname()[1]

    def _platform_addresses(self) -> list[str]:
        host = self.config.telemetry_host
        if not host:
            return []
        found: list[str] = []
        try:
            for info in socket.getaddrinfo(host, self.config.telemetry_port, type=socket.SOCK_STREAM):
                address = info[4][0]
                if address not in found:
                    found.append(address)
        except OSError:
            log.debug("could not resolve %s for the admin allowlist", host)
        mine = local_address_towards(host, self.config.telemetry_port, default=None)
        if mine and mine not in found:
            found.append(mine)
        return found

    def _admin_host(self) -> str:
        """Resolve ``auto`` to the address facing the reporting endpoint.

        Falls back to loopback rather than to 0.0.0.0: if we cannot prove which interface
        is the internal one, the safe failure is unreachable, not readable by everyone on
        the application network."""
        configured = self.config.admin_host
        if configured != "auto":
            return configured
        host = self.config.telemetry_host
        address = local_address_towards(host, self.config.telemetry_port, default=None) if host else None
        if address is None:
            log.warning("admin API bound to loopback: internal address undetermined")
            return "127.0.0.1"
        return address

    async def _teardown(self) -> None:
        for server in self._servers:
            server.close()
        for server in self._servers:
            with contextlib.suppress(Exception):
                await server.wait_closed()
        self._servers.clear()
        if self._udp_transport is not None:
            self._udp_transport.close()
            self._udp_transport = None
        self.poller.stop()
        if self.worker is not None:
            self.worker.stop()
        self.telemetry.stop()


def _quiet_exception_handler(loop, context) -> None:  # pragma: no cover - noise control
    exc = context.get("exception")
    if isinstance(exc, (ConnectionResetError, BrokenPipeError, TimeoutError)):
        return
    if exc is not None and exc.__class__.__module__ == "ssl":
        log.debug("tls error: %s", exc)
        return
    log.debug("event loop: %s", context.get("message"))
