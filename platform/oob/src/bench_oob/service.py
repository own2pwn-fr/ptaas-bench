"""Wires the five listeners, the store, the collector client and the control API into
one process on one event loop.

Two ways to run it, sharing all of the setup:

* ``OobService(config).run()`` -- blocking, used by ``python -m bench_oob``;
* ``start()`` / ``stop()`` -- spins the loop in a background thread, which is how the
  tests drive it with ordinary blocking sockets.

Every listener is optional in the sense that a port set to 0 is still bound (ephemeral,
for tests) but a listener that fails to bind is fatal, except HTTPS: if certificate
generation fails we log and carry on, because four working channels beat none.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import threading
from urllib.parse import urlsplit

from .collector import CollectorClient
from .config import Config
from .listeners.control import ControlHandler
from .listeners.dns import DnsHandler, DnsUdpProtocol, handle_tcp
from .listeners.ldap import LdapHandler
from .listeners.smtp import SmtpHandler
from .listeners.web import WebHandler
from .net import local_address_towards
from .recorder import Recorder
from .store import CallbackStore

log = logging.getLogger("bench_oob.service")


class OobService:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.store = CallbackStore(maxlen=config.store_size)
        self.collector = CollectorClient(
            config.collector_url,
            queue_size=config.queue_size,
            batch_size=config.batch_size,
            flush_interval=config.flush_interval,
            timeout=config.collector_timeout,
        )
        self.recorder = Recorder(config, self.store, self.collector)
        self.ports: dict[str, int] = {}
        self.https_error: str | None = None

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

    def start(self, timeout: float = 20.0) -> "OobService":
        if self._thread is not None:
            return self
        self._ready.clear()
        self._thread = threading.Thread(
            target=lambda: asyncio.run(self._amain(install_signals=False)),
            name="oob-service",
            daemon=True,
        )
        self._thread.start()
        if not self._ready.wait(timeout):
            raise RuntimeError("oob service did not start in time")
        if self._error is not None:
            raise RuntimeError(f"oob service failed to start: {self._error}")
        return self

    def stop(self, timeout: float = 10.0) -> None:
        loop, event = self._loop, self._stop_event
        if loop is not None and event is not None and not loop.is_closed():
            loop.call_soon_threadsafe(event.set)
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
        self._thread = None

    def __enter__(self) -> "OobService":
        return self.start()

    def __exit__(self, *exc_info) -> None:
        self.stop()

    # -- internals --------------------------------------------------------------

    async def _amain(self, install_signals: bool) -> None:
        self._loop = asyncio.get_running_loop()
        self._stop_event = asyncio.Event()
        # TLS handshake failures from a scanner speaking plain HTTP to :443 are normal
        # traffic here, not bugs; keep them out of stderr.
        self._loop.set_exception_handler(_quiet_exception_handler)
        if install_signals:
            for sig in (signal.SIGINT, signal.SIGTERM):
                with contextlib.suppress(NotImplementedError):
                    self._loop.add_signal_handler(sig, self._stop_event.set)
        self.collector.start()
        try:
            await self._setup()
        except BaseException as exc:  # noqa: BLE001 - reported to the starting thread
            self._error = exc
            self._ready.set()
            await self._teardown()
            raise
        self._ready.set()
        log.info(
            "oob canary up for %s: %s", self.config.domain, self.ports
        )
        try:
            await self._stop_event.wait()
        finally:
            await self._teardown()

    async def _setup(self) -> None:
        cfg = self.config
        loop = asyncio.get_running_loop()
        dns_handler = DnsHandler(cfg, self.recorder)

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
        self.ports["control"] = await self._serve(
            ControlHandler(cfg, self.store, self.collector),
            self._control_host(),
            cfg.control_port,
        )

        try:
            from .certs import server_context

            context = await loop.run_in_executor(None, server_context, cfg.domain)
            self.ports["https"] = await self._serve(
                WebHandler(cfg, self.recorder, "https"),
                cfg.listen_host,
                cfg.https_port,
                ssl=context,
            )
        except Exception as exc:  # noqa: BLE001 - HTTPS is the one optional channel
            self.https_error = f"{type(exc).__name__}: {exc}"
            log.error("HTTPS listener disabled: %s", self.https_error)

    async def _serve(self, handler, host: str, port: int, **kwargs) -> int:
        server = await asyncio.start_server(handler, host, port, **kwargs)
        self._servers.append(server)
        return server.sockets[0].getsockname()[1]

    def _control_host(self) -> str:
        """Resolve ``auto`` to the address facing the collector, i.e. bench-internal.

        Falls back to loopback rather than to 0.0.0.0: if we cannot prove which
        interface is internal, the safe failure is unreachable, not world-readable."""
        configured = self.config.control_host
        if configured != "auto":
            return configured
        host = self.config.collector_host
        if host:
            port = urlsplit(self.config.collector_url).port or 8900
            address = local_address_towards(host, port, default=None)
        else:
            address = None
        if address is None:
            log.warning(
                "control API bound to loopback: could not determine the bench-internal "
                "address (collector_url=%r)",
                self.config.collector_url,
            )
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
        self.collector.stop()


def _quiet_exception_handler(loop, context) -> None:  # pragma: no cover - noise control
    exc = context.get("exception")
    if isinstance(exc, (ConnectionResetError, BrokenPipeError, TimeoutError)):
        return
    if exc is not None and exc.__class__.__module__ == "ssl":
        log.debug("tls error: %s", exc)
        return
    log.debug("event loop: %s", context.get("message"))
