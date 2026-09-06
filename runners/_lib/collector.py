"""Client for the collector run lifecycle (platform/collector/openapi.yaml).

Only the four calls the harness needs: open a run, read the active run, count what
the tool has done so far, close the run. The collector is the timekeeper of record:
the run id it hands back is what every event is stamped with, so the harness must
open the run *after* the target reset and close it *before* the next tool starts, or
findings get attributed to the wrong scanner.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .internal_http import Http, HttpError, Response

log = logging.getLogger("bench.runners.collector")

# Loopback, not a service name: the control endpoints answer only to the collector's
# own loopback and to the sinkhole, and the orchestrator reaches them by executing
# inside the collector's container. Nothing is published to the host.
DEFAULT_BASE_URL = "http://127.0.0.1:8900"


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
        # None until a run is opened with an address map; then True/False.
        self.addresses_accepted: bool | None = None

    def _call(self, method: str, path: str, **kwargs: Any) -> Response:
        return self.http.request(method, f"{self.base_url}{path}", **kwargs)

    @staticmethod
    def _explain_404(what: str) -> str:
        """A 404 on a control endpoint is almost never a missing route.

        The collector answers 404 to any caller outside TELEMETRY_CONTROL_CIDRS, so
        the usual cause is talking to it from the wrong place -- a service name
        instead of its loopback, or a transport that is not `docker compose exec`
        into the collector itself.
        """
        return (
            f"{what}: HTTP 404. The control endpoints answer 404 (not 403) to any "
            "caller outside TELEMETRY_CONTROL_CIDRS, so this most likely means the "
            "request did not come from the collector's own loopback. Check "
            "--collector-transport and --collector-url."
        )

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
        addresses: dict[str, Any] | None = None,
    ) -> Run:
        """POST /v1/runs.

        ``force`` is not exposed as a convenience: a 409 means a previous run was
        never closed, i.e. some events in the database belong to a scan nobody
        accounted for. The operator should be told, not have it silently overwritten.

        ``addresses`` is the target address map (see _lib/topology.py). The collector
        is gaining an explicit field for it; until it does, ``RunCreate`` forbids
        unknown properties, so a rejection is retried once without the map rather
        than failing the run. Whether it was accepted is recorded either way: the
        collector cannot attribute an out-of-band callback without it, and a run
        where it was dropped has weaker blind-vulnerability attribution than one
        where it was not.
        """
        body: dict[str, Any] = {
            "tool": tool,
            "tool_version": tool_version,
            "profile": profile,
            "targets": targets,
            "notes": notes,
            "force": force,
        }
        body = {k: v for k, v in body.items() if v is not None}
        if addresses:
            body["addresses"] = addresses

        res = self._call("POST", "/v1/runs", json_body=body)
        if addresses and res.status in (400, 422):
            log.warning(
                "the collector rejected the target address map (HTTP %s). Retrying "
                "without it: out-of-band callbacks will have to be attributed by "
                "whatever the collector can infer, which is unreliable for dual-homed "
                "targets. The map is still written to the run record.",
                res.status,
            )
            self.addresses_accepted = False
            body.pop("addresses")
            res = self._call("POST", "/v1/runs", json_body=body)
        elif addresses:
            self.addresses_accepted = res.ok

        if res.status == 404:
            raise HttpError(self._explain_404("open run"))
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
        if res.status == 404:
            raise HttpError(self._explain_404(f"close run {run_id}"))
        return Run.from_json(res.check("close run").json())

    def stats(self) -> dict[str, Any]:
        """Ingest diagnostics, recorded with every run.

        `discarded_idle` is the counter that explains an implausibly low event count:
        well-formed events arriving while no run was active. The other way to get a
        low count is traffic being classified as the platform's own by source
        address, which is why the run record also states where our own requests came
        from.
        """
        res = self._call("GET", "/v1/stats")
        if not res.ok:
            return {"error": f"HTTP {res.status}", "note": self._explain_404("stats")}
        try:
            return res.json()
        except HttpError:
            return {"error": "unparsable stats response"}

    def event_count(self) -> int:
        """Cheap metering signal: the active run's total event count.

        Used for the request budget while the scan is running. It counts every event
        type, not just http_request, so it slightly over-counts -- triggers and OOB
        callbacks are a rounding error next to request volume, and the exact figure
        is recomputed by ``count_http_requests`` for the record.
        """
        run = self.active_run()
        return run.event_count if run else 0

    def export_events(
        self,
        run_id: str,
        path: Path,
        *,
        in_scope: Callable[[dict[str, Any]], bool] | None = None,
        out_of_scope_path: Path | None = None,
        page: int = 5000,
    ) -> dict[str, Any]:
        """Write the run's whole event stream to disk as JSONL.

        The events ARE the ground truth: reach, exercise and trigger are all derived
        from them, and a run directory without them cannot be re-scored by anyone who
        does not have the collector in front of them. Since the collector answers
        only to its own loopback -- correctly, because it holds the answer key -- the
        scorer cannot fetch them itself, so the harness exports them at close through
        the same exec transport it uses for the control plane.

        ``in_scope`` partitions the stream. The resolver is shared, so a target that
        is not in this run's scope can emit a DNS lookup while a run is open and land
        it in the record; those belong in a separate file, not in a published run as
        though the tool caused them.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        seq = 0
        kept = dropped = 0
        by_type: dict[str, int] = {}
        by_app: dict[str, int] = {}
        requests_by_app: dict[str, int] = {}
        out_handle = out_of_scope_path.open("w", encoding="utf-8") if out_of_scope_path else None
        try:
            with path.open("w", encoding="utf-8") as handle:
                while True:
                    res = self._call(
                        "GET", f"/v1/runs/{run_id}/events?after_seq={seq}&limit={page}"
                    )
                    if not res.ok:
                        log.error(
                            "event export failed at seq %s (HTTP %s). The run directory is "
                            "incomplete and the run cannot be re-scored from it.",
                            seq, res.status,
                        )
                        break
                    payload = res.json()
                    events = payload.get("events") or []
                    for event in events:
                        if isinstance(event.get("seq"), int):
                            seq = max(seq, event["seq"])
                        line = json.dumps(event, sort_keys=True)
                        if in_scope is not None and not in_scope(event):
                            dropped += 1
                            if out_handle is not None:
                                out_handle.write(line + "\n")
                            continue
                        handle.write(line + "\n")
                        kept += 1
                        kind = str(event.get("type", "unknown"))
                        by_type[kind] = by_type.get(kind, 0) + 1
                        app = str(event.get("app") or "")
                        if app:
                            by_app[app] = by_app.get(app, 0) + 1
                            if kind == "http_request" and not event.get("synthetic"):
                                requests_by_app[app] = requests_by_app.get(app, 0) + 1
                    next_seq = payload.get("next_seq")
                    if next_seq:
                        seq = max(seq, int(next_seq))
                    if not events or len(events) < page:
                        break
        finally:
            if out_handle is not None:
                out_handle.close()
        return {
            "file": path.name,
            "events": kept,
            "by_type": by_type,
            "by_app": by_app,
            "requests_by_app": requests_by_app,
            "out_of_scope": dropped,
            "out_of_scope_file": out_of_scope_path.name if out_of_scope_path else None,
            "last_seq": seq,
        }

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
