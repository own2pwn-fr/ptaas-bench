"""Fire-and-forget client for the collector's POST /v1/events.

Same discipline as the target SDKs, and for the same reason: a listener must behave
identically whether the collector is fast, slow, down or absent. Anything else and the
canary itself becomes a timing side-channel, or worse, drops callbacks when the
platform hiccups.

Mechanism: a bounded queue plus one daemon thread. ``submit`` is non-blocking and
never raises; when the queue is full the event is dropped and counted. The thread
batches, POSTs with a short timeout, and on failure drops the batch rather than
retrying forever -- the store keeps the evidence either way, and a retry storm during
an outage would be worse than a gap.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("bench_oob.collector")


@dataclass
class CollectorStats:
    enqueued: int = 0
    dropped: int = 0
    posted: int = 0
    failed: int = 0
    last_error: str | None = field(default=None)

    def as_json(self) -> dict[str, Any]:
        return {
            "enqueued": self.enqueued,
            "dropped": self.dropped,
            "posted": self.posted,
            "failed": self.failed,
            "last_error": self.last_error,
        }


class CollectorClient:
    def __init__(
        self,
        base_url: str,
        *,
        queue_size: int = 2000,
        batch_size: int = 100,
        flush_interval: float = 0.5,
        timeout: float = 2.0,
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self.timeout = timeout
        self.stats = CollectorStats()
        self._queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=queue_size)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self.base_url)

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="oob-collector-flush", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
        self._thread = None

    def submit(self, event: dict[str, Any]) -> bool:
        """Queue one event. Returns False when it was dropped; never blocks, never raises."""
        with self._lock:
            self.stats.enqueued += 1
        if not self.enabled:
            # No collector configured (unit tests, local debugging): the store is still
            # authoritative, so this is a supported mode rather than an error.
            with self._lock:
                self.stats.dropped += 1
            return False
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            with self._lock:
                self.stats.dropped += 1
            return False
        return True

    def flush(self, timeout: float = 2.0) -> None:
        """Best-effort drain, for tests and for a clean shutdown."""
        deadline = time.monotonic() + timeout
        while not self._queue.empty() and time.monotonic() < deadline:
            time.sleep(0.02)

    # -- background thread ------------------------------------------------------

    def _run(self) -> None:
        while not self._stop.is_set():
            batch = self._collect_batch()
            if batch:
                self._post(batch)
        # Drain what is left so a graceful shutdown does not lose the tail.
        batch = self._collect_batch(block=False)
        if batch:
            self._post(batch)

    def _collect_batch(self, block: bool = True) -> list[dict[str, Any]]:
        batch: list[dict[str, Any]] = []
        deadline = time.monotonic() + self.flush_interval
        while len(batch) < self.batch_size:
            remaining = deadline - time.monotonic()
            try:
                if block and not batch:
                    batch.append(self._queue.get(timeout=min(self.flush_interval, 0.25)))
                elif block and remaining > 0:
                    batch.append(self._queue.get(timeout=remaining))
                else:
                    batch.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return batch

    def _post(self, batch: list[dict[str, Any]]) -> None:
        body = json.dumps({"events": batch}).encode()
        request = urllib.request.Request(
            f"{self.base_url}/v1/events",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                response.read()
            with self._lock:
                self.stats.posted += len(batch)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            # Deliberately terminal: drop the batch, remember why, carry on serving.
            with self._lock:
                self.stats.failed += len(batch)
                self.stats.last_error = f"{type(exc).__name__}: {exc}"
            log.debug("collector POST failed, dropping %d events: %s", len(batch), exc)
