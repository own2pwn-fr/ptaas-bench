"""Turns a raw listener observation into a stored record and a reported event.

Every listener funnels through here so that the identifier rules, the attribution join,
the synthetic-source test, the event shape and the truncation limits live in one place.

Timing rule that shapes the flow: the store record is written before anything else, and
the listener returns immediately. When the local index cannot attribute the request, the
record is handed to the attribution worker, which does one targeted lookup and then
reports the event. Nothing on a listener's path ever waits for the network.
"""

from __future__ import annotations

import time
from typing import Any, Iterable

from .config import Config
from .correlation import (
    HIGH,
    LOW,
    MODE_OWNED_LABEL,
    MODE_UNATTRIBUTED,
    NONE,
    Attribution,
    CorrelationIndex,
)
from .store import Observation, ObservationStore
from .telemetry import EVENT_KIND
from .tokens import UNIDENTIFIED, Candidate, extract

RAW_MAX = 2048  # `raw` maxLength in the reporting protocol.


class Recorder:
    def __init__(
        self,
        config: Config,
        store: ObservationStore,
        telemetry: Any,
        index: CorrelationIndex,
        worker: Any | None = None,
    ) -> None:
        self.config = config
        self.store = store
        self.telemetry = telemetry
        self.index = index
        self.worker = worker

    def record(
        self,
        *,
        channel: str,
        source_ip: str,
        candidates: Iterable[Candidate],
        raw: str,
        host: str | None = None,
        detail: dict[str, Any] | None = None,
        owned_zone: bool | None = None,
    ) -> Observation:
        now = time.time()
        extraction = extract(candidates)
        attribution = self._attribute(host, source_ip, extraction.found, owned_zone, now)

        known = extraction.found and owned_zone is not False
        if known and self.config.known_tokens:
            # An explicit allowlist is optional: without it we cannot tell a label we
            # planted from a plausible-looking foreign one, and this service does not
            # read the catalog.
            known = extraction.token in self.config.known_tokens

        observation = self.store.add(
            ts=now,
            channel=channel,
            host=host,
            token=extraction.token,
            nonce=extraction.nonce,
            source=extraction.source,
            raw_token=extraction.label or None,
            owned_zone=owned_zone,
            known=known,
            source_ip=source_ip,
            synthetic=self.config.is_synthetic_source(source_ip),
            app=attribution.app,
            confidence=attribution.confidence,
            attribution=attribution.as_json(),
            raw=_annotate(raw, extraction.source, owned_zone, attribution.confidence),
            detail=detail or {},
        )

        # Hand it to the attribution worker only when a targeted lookup could actually
        # improve things: there is a host to ask about, and what we have is weak.
        deferred = bool(
            self.worker is not None
            and host
            and attribution.confidence in (LOW, NONE)
            and self.worker.enqueue(observation, host, source_ip)
        )
        if not deferred:
            self.report(observation)
        return observation

    def _attribute(
        self, host: str | None, source_ip: str, found: bool, owned_zone: bool | None, now: float
    ) -> Attribution:
        attribution = self.index.match(host, source_ip, now)
        if attribution.mode == MODE_UNATTRIBUTED and found and owned_zone:
            # A label under the zone we own is as good as a hint: the payload template
            # named us on purpose, so the mapping back to the catalog is direct.
            return Attribution(mode=MODE_OWNED_LABEL, confidence=HIGH, app=None)
        return attribution

    def finalise(self, observation: Observation, attribution: Attribution) -> None:
        """Apply a late attribution and report. Called by the attribution worker."""
        if attribution.mode != MODE_UNATTRIBUTED and attribution.confidence != observation.confidence:
            self.store.update(
                observation,
                app=attribution.app or observation.app,
                confidence=attribution.confidence,
                attribution=attribution.as_json(),
                raw=_reannotate(observation.raw, attribution.confidence),
            )
        self.report(observation)

    def report(self, observation: Observation) -> None:
        self.telemetry.submit(
            {
                "type": EVENT_KIND,
                # The application the request is attributed to when the join succeeded,
                # otherwise this service's own key, so the event is never orphaned.
                "app": observation.app or self.config.app,
                "ts": observation.ts,
                "synthetic": observation.synthetic,
                # The schema requires a string here, so a request with no identifier
                # gets a sentinel and the recognisable parts go to `raw` and the keys.
                "token": observation.token or UNIDENTIFIED,
                "channel": observation.channel,
                "source_ip": observation.source_ip,
                # Same value under the name every event type carries, which is also what
                # the reporting endpoint tests against its own synthetic networks.
                "client_ip": observation.source_ip,
                "raw": observation.raw,
                # Join keys, so the endpoint can redo the correlation at export time and
                # upgrade an attribution we made before a hint was registered.
                "observed_host": observation.host or None,
                "confidence": observation.confidence or NONE,
                "attribution": observation.attribution,
            }
        )


def _annotate(raw: str, source: str | None, owned_zone: bool | None, confidence: str) -> str:
    origin = "n/a" if owned_zone is None else ("owned" if owned_zone else "external")
    suffix = f" | src={source or 'none'} origin={origin} conf={confidence or NONE}"
    room = RAW_MAX - len(suffix)
    return raw[:room] + suffix


def _reannotate(raw: str, confidence: str) -> str:
    head, sep, _ = raw.rpartition(" conf=")
    return f"{head}{sep}{confidence or NONE}" if sep else raw
