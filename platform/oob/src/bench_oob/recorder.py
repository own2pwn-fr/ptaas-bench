"""Turns a raw listener observation into a stored callback and a collector event.

Every listener funnels through here so that the token rules, the event shape and the
truncation limits live in exactly one place.
"""

from __future__ import annotations

import time
from typing import Any, Iterable

from .config import Config
from .store import Callback, CallbackStore
from .tokens import UNKNOWN_TOKEN, Candidate, extract

RAW_MAX = 2048  # OobEvent.raw maxLength in the collector's OpenAPI.


class Recorder:
    def __init__(self, config: Config, store: CallbackStore, collector: Any) -> None:
        self.config = config
        self.store = store
        self.collector = collector

    def record(
        self,
        *,
        channel: str,
        source_ip: str,
        candidates: Iterable[Candidate],
        raw: str,
        detail: dict[str, Any] | None = None,
        in_zone: bool | None = None,
        synthetic: bool = False,
    ) -> Callback:
        extraction = extract(candidates)
        known = extraction.found and in_zone is not False
        if known and self.config.known_tokens:
            # An explicit allowlist (BENCH_OOB_KNOWN_TOKENS) is optional: without it we
            # cannot tell a planted token from a plausible-looking foreign one, and the
            # service does not read the catalog (only the scoring engine does).
            known = extraction.token in self.config.known_tokens

        annotated = _annotate(raw, extraction.source, in_zone, extraction.found)
        callback = self.store.add(
            ts=time.time(),
            channel=channel,
            token=extraction.token,
            nonce=extraction.nonce,
            source=extraction.source,
            raw_token=extraction.label or None,
            in_zone=in_zone,
            known=known,
            source_ip=source_ip,
            raw=annotated,
            detail=detail or {},
        )
        self.collector.submit(
            {
                "type": "oob",
                "app": self.config.app,
                "ts": callback.ts,
                "synthetic": synthetic,
                # The schema requires a string, so an unrecognised callback is reported
                # under a sentinel; everything needed to identify it is in `raw`.
                "token": callback.token or UNKNOWN_TOKEN,
                "channel": channel,
                "source_ip": source_ip,
                "raw": annotated,
            }
        )
        return callback


def _annotate(raw: str, source: str | None, in_zone: bool | None, found: bool) -> str:
    zone = "n/a" if in_zone is None else ("self" if in_zone else "foreign")
    suffix = f" | src={source or 'none'} zone={zone}"
    if not found:
        suffix += " token=unknown"
    room = RAW_MAX - len(suffix)
    return raw[:room] + suffix
