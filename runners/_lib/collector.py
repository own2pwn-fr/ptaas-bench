"""Client for the collector run lifecycle (platform/collector/openapi.yaml).

Only the four calls the harness needs: open a run, read the active run, count what
the tool has done so far, close the run. The collector is the timekeeper of record:
the run id it hands back is what every event is stamped with, so the harness must
open the run *after* the target reset and close it *before* the next tool starts, or
findings get attributed to the wrong scanner.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .internal_http import Http, HttpError, Response

log = logging.getLogger("bench.runners.collector")

DEFAULT_BASE_URL = "http://collector:8900"


@dataclass
class Run:
    run_id: str
    tool: str
    tool_version: str | None = None
    profile: str | None = None
    targets: list[str] = field(default_factory=list)
    started_at: str | None = None
    closed_at: str | None = None
    active: bool = True
    event_count: int = 0
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> Run:
        return cls(
            run_id=str(payload.get("run_id")),
            tool=str(payload.get("tool", "")),
            tool_version=payload.get("tool_version"),
            profile=payload.get("profile"),
            targets=list(payload.get("targets") or []),
            started_at=payload.get("started_at"),
            closed_at=payload.get("closed_at"),
            active=bool(payload.get("active", False)),
            event_count=int(payload.get("event_count") or 0),
            raw=payload,
        )


class CollectorClient:
    def __init__(self, http: Http, base_url: str = DEFAULT_BASE_URL):
        self.http = http
        self.base_url = base_url.rstrip("/")
        # Cursor for the incremental export. Kept per instance so repeated polls
        # during a run stay O(new events) rather than re-downloading the whole run.
        self._seq_cursor: dict[str, int] = {}
        self._request_counts: dict[str, int] = {}

    def _call(self, method: str, path: str, **kwargs: Any) -> Response:
        return self.http.request(method, f"{self.base_url}{path}", **kwargs)

    def healthz(self) -> bool:
        return self._call("GET", "/healthz").ok

    def open_run(
        self,
        *,
        tool: str,
        tool_version: str | None,
        profile: str | None,
        targets: list[str],
        notes: str | None = None,
        force: bool = False,
    ) -> Run:
        """POST /v1/runs.

        ``force`` is not exposed as a convenience: a 409 means a previous run was
        never closed, i.e. some events in the database belong to a scan nobody
        accounted for. The operator should be told, not have it silently overwritten.
        """
        body = {
            "tool": tool,
            "tool_version": tool_version,
            "profile": profile,
            "targets": targets,
            "notes": notes,
            "force": force,
        }
        body = {k: v for k, v in body.items() if v is not None}
        res = self._call("POST", "/v1/runs", json_body=body)
        if res.status == 409:
            raise HttpError(
                "the collector already has an active run. Close it (or re-run with "
                "--force) -- events from an abandoned run would be attributed to this one."
            )
        return Run.from_json(res.check("open run").json())

    def active_run(self) -> Run | None:
        res = self._call("GET", "/v1/runs/active")
        if res.status == 404:
            return None
        return Run.from_json(res.check("active run").json())

    def close_run(self, run_id: str) -> Run:
        res = self._call("POST", f"/v1/runs/{run_id}/close")
        return Run.from_json(res.check("close run").json())

    def event_count(self) -> int:
        """Cheap metering signal: the active run's total event count.

        Used for the request budget while the scan is running. It counts every event
        type, not just http_request, so it slightly over-counts -- triggers and OOB
        callbacks are a rounding error next to request volume, and the exact figure
        is recomputed by ``count_http_requests`` for the record.
        """
        run = self.active_run()
        return run.event_count if run else 0

    def count_http_requests(self, run_id: str, *, page: int = 5000) -> int:
        """Exact number of non-synthetic http_request events seen for this run.

        Incremental: only pages added since the last call are fetched, so calling it
        during the run is cheap. Synthetic events (seeding, health checks, self-test)
        are excluded -- crediting the platform's own traffic to the tool would be the
        easiest way to make a benchmark lie.
        """
        seq = self._seq_cursor.get(run_id, 0)
        total = self._request_counts.get(run_id, 0)
        while True:
            res = self._call(
                "GET",
                f"/v1/runs/{run_id}/events?type=http_request&after_seq={seq}&limit={page}",
            )
            if not res.ok:
                log.warning("event export failed (HTTP %s); keeping last count", res.status)
                break
            payload = res.json()
            events = payload.get("events") or []
            total += sum(1 for ev in events if not ev.get("synthetic"))
            next_seq = payload.get("next_seq")
            for ev in events:
                if isinstance(ev.get("seq"), int):
                    seq = max(seq, ev["seq"])
            if next_seq:
                seq = max(seq, int(next_seq))
            if not events or len(events) < page:
                break
        self._seq_cursor[run_id] = seq
        self._request_counts[run_id] = total
        return total
