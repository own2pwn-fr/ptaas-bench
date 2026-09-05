"""Run bookkeeping and the buffered event writer.

Ingestion is split in two halves on purpose:

* the request path (``submit``) only validates and enqueues -- no awaits on the
  database. Instrumentation sits inside the target's request handling, so any
  latency the collector adds shows up as a timing artefact and biases timing-based
  oracles. The active run is resolved from memory for the same reason.
* a single background task drains the queue and writes batches. Being single means
  ``seq`` can be handed out from an in-process counter without a lock or a
  round-trip, and it is monotonic per run by construction.

``flush()`` exists so the read paths (event export, run close, stats) can wait for
the buffer to land before answering; without it the scorer could export a run and
miss the last few hundred events.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import json
import logging
import uuid
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from .config import Settings
from .correlations import CorrelationRegistry
from .models import Base, Event, Run
from .schemas import MAX_EVENTS_PER_BATCH, AnyEvent, CorrelationCreate, dump_event

log = logging.getLogger("bench.collector")

_EventAdapter: Any = None


def _event_adapter():
    # Built once, lazily: a TypeAdapter over the discriminated union is what makes
    # per-event validation cheap enough to run inline on the request path.
    global _EventAdapter
    if _EventAdapter is None:
        from pydantic import TypeAdapter

        _EventAdapter = TypeAdapter(AnyEvent)
    return _EventAdapter


@dataclass(slots=True)
class QueuedEvent:
    run_id: str
    received_at: datetime
    type: str
    app: str
    ts: float | None
    synthetic: bool
    payload: dict[str, Any]


def _now() -> datetime:
    return datetime.now(UTC)


def parse_address(raw: Any) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """Best-effort address extraction from whatever an SDK put in the field.

    Real deployments hand us ``1.2.3.4``, ``1.2.3.4:51234``, ``[::1]:80`` and
    forwarded-for lists. Getting this wrong in either direction is expensive: a
    missed match scores our own seeding traffic as if a tool had produced it.
    """
    if raw in (None, ""):
        return None
    text = str(raw).split(",")[0].strip()
    if text.startswith("["):  # [::1]:80
        text = text[1:].split("]")[0]
    elif text.count(":") == 1:  # host:port, never bare IPv6
        text = text.split(":")[0]
    try:
        return ipaddress.ip_address(text)
    except ValueError:
        return None


class Collector:
    """Owns the engine, the active-run state and the writer task."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.engine: AsyncEngine | None = None
        self.session_factory: async_sessionmaker | None = None
        self.queue: asyncio.Queue[list[QueuedEvent]] = asyncio.Queue(maxsize=settings.queue_maxsize)
        self._writer: asyncio.Task | None = None
        self._run_lock = asyncio.Lock()
        self.active_run_id: str | None = None
        self._seq: dict[str, int] = {}
        self.counters: Counter[str] = Counter()
        self.correlations = CorrelationRegistry(
            ttl=settings.correlation_ttl, max_entries=settings.correlation_max
        )

    # ------------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        connect_args: dict[str, Any] = {}
        if self.settings.database_url.startswith("sqlite"):
            # The writer task and the read paths use separate connections; give
            # SQLite room to serialise them instead of failing with "locked".
            connect_args["timeout"] = 30
        self.engine = create_async_engine(
            self.settings.database_url,
            echo=self.settings.sql_echo,
            pool_pre_ping=True,
            connect_args=connect_args,
        )
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await self._restore_state()
        self._writer = asyncio.create_task(self._writer_loop(), name="bench-collector-writer")

    async def stop(self) -> None:
        with contextlib.suppress(Exception):
            await self.flush()
        if self._writer is not None:
            self._writer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._writer
            self._writer = None
        if self.engine is not None:
            await self.engine.dispose()
            self.engine = None

    async def _restore_state(self) -> None:
        """Re-adopt the active run after a collector restart.

        A crash mid-run must not orphan the events that keep arriving: targets never
        send a run id, so if the in-memory pointer were lost every subsequent event
        would be discarded as "idle".
        """
        assert self.session_factory is not None
        async with self.session_factory() as session:
            run = (
                await session.execute(select(Run).where(Run.active.is_(True)).order_by(Run.started_at.desc()))
            ).scalars().first()
            if run is not None:
                self.active_run_id = run.run_id
                last = (
                    await session.execute(select(func.max(Event.seq)).where(Event.run_id == run.run_id))
                ).scalar()
                self._seq[run.run_id] = int(last or 0)

    def is_synthetic_source(self, event: Any) -> bool:
        """True when the event was caused by the platform's own traffic.

        Identified by source address, never by a marker header: any reflection,
        verbose error or header-injection flaw in a target would have shown the tool
        a header named after the grader, and told it exactly what it was inside of.
        """
        if not self.settings.synthetic_networks:
            return False
        extra = event.model_extra or {}
        for attr in ("client_ip", "source_ip"):
            address = parse_address(getattr(event, attr, None) or extra.get(attr))
            if address is None:
                continue
            if any(address in network for network in self.settings.synthetic_networks):
                return True
        return False

    # ---------------------------------------------------------------- correlations

    def register_correlation(self, spec: CorrelationCreate) -> dict[str, Any]:
        """Register an outbound-fetch hint and mirror it into the event stream.

        Registration is synchronous in memory because the callback it describes can
        arrive within milliseconds; the event copy goes through the ordinary buffered
        path so the caller -- a planted sink, mid-request -- still pays no database
        latency.
        """
        record = spec.model_dump(mode="json", by_alias=True)
        record["type"] = "correlation"
        if self.is_synthetic_source(spec):
            record["synthetic"] = True
        entry = self.correlations.register(record, ttl=spec.ttl)
        self.counters["correlations"] += 1
        # Counted like any other intake so the debug totals still reconcile:
        # received == written + dropped + discarded_idle.
        self.counters["received"] += 1
        self._enqueue_payload(entry)
        return entry

    def _enqueue_payload(self, payload: dict[str, Any]) -> bool:
        """Queue an already-validated payload. Returns False when nothing was kept."""
        run_id = self.active_run_id
        if run_id is None:
            self.counters["discarded_idle"] += 1
            return False
        item = QueuedEvent(
            run_id=run_id,
            received_at=_now(),
            type=str(payload.get("type")),
            app=str(payload.get("app", "")),
            ts=payload.get("ts"),
            synthetic=bool(payload.get("synthetic")),
            payload=payload,
        )
        try:
            self.queue.put_nowait([item])
        except asyncio.QueueFull:
            self.counters["dropped_overflow"] += 1
            log.error("event queue full (maxsize=%d), dropped 1 event", self.queue.maxsize)
            return False
        return True

    # ----------------------------------------------------------------------- runs

    async def open_run(self, spec, force: bool) -> tuple[Run | None, bool]:
        """Open a run. Returns ``(run, ok)``; ``ok=False`` means 409."""
        assert self.session_factory is not None
        async with self._run_lock:
            if self.active_run_id is not None:
                if not force:
                    return None, False
                await self._close_run_locked(self.active_run_id)
            run = Run(
                run_id=uuid.uuid4().hex,
                tool=spec.tool,
                tool_version=spec.tool_version,
                profile=spec.profile,
                targets=list(spec.targets or []),
                notes=spec.notes,
                started_at=_now(),
                closed_at=None,
                active=True,
                event_count=0,
            )
            async with self.session_factory() as session:
                session.add(run)
                await session.commit()
            self._seq[run.run_id] = 0
            self.active_run_id = run.run_id
            return run, True

    async def close_run(self, run_id: str) -> Run | None:
        # Land buffered events before flipping the run closed, so an export taken
        # immediately after close is complete.
        await self.flush()
        async with self._run_lock:
            return await self._close_run_locked(run_id)

    async def _close_run_locked(self, run_id: str) -> Run | None:
        assert self.session_factory is not None
        async with self.session_factory() as session:
            run = await session.get(Run, run_id)
            if run is None:
                return None
            if run.active:
                run.active = False
                run.closed_at = _now()
                await session.commit()
        if self.active_run_id == run_id:
            self.active_run_id = None
        return run

    async def get_run(self, run_id: str) -> Run | None:
        assert self.session_factory is not None
        await self.flush()
        async with self.session_factory() as session:
            return await session.get(Run, run_id)

    async def list_runs(self) -> list[Run]:
        assert self.session_factory is not None
        await self.flush()
        async with self.session_factory() as session:
            return list((await session.execute(select(Run).order_by(Run.started_at.desc()))).scalars())

    # --------------------------------------------------------------------- events

    def submit(self, raw_events: Iterable[Any]) -> dict[str, int]:
        """Validate and enqueue a batch. Never raises, never awaits the database."""
        run_id = self.active_run_id
        received_at = _now()
        accepted: list[QueuedEvent] = []
        dropped = 0
        idle = 0
        over_batch = 0

        for index, raw in enumerate(raw_events):
            if index >= MAX_EVENTS_PER_BATCH:
                over_batch += 1
                continue
            try:
                event = _event_adapter().validate_python(raw)
            except ValidationError as exc:
                dropped += 1
                # Loud on purpose: a broken SDK degrades ground truth silently
                # otherwise, and a run scored on partial data is worse than no run.
                log.error(
                    "dropping malformed event: %s | raw=%s",
                    exc.errors(include_url=False, include_input=False),
                    _preview(raw),
                )
                continue
            except Exception:  # defensive: never fail the caller
                dropped += 1
                log.exception("dropping unprocessable event: %s", _preview(raw))
                continue
            synthetic = bool(event.synthetic) or self.is_synthetic_source(event)
            if synthetic and not event.synthetic:
                self.counters["synthetic_by_source"] += 1
            payload = dump_event(event)
            payload["synthetic"] = synthetic

            if event.type == "correlation":
                # Same door, same registry: an SDK may batch a hint with its other
                # telemetry rather than call POST /v1/correlations, and the sinkhole
                # must see it either way.
                entry = self.correlations.register(payload, ttl=payload.get("ttl"))
                self.counters["correlations"] += 1
                payload = entry

            if run_id is None:
                # Idle: targets stay instrumented between benchmarks so their
                # behaviour is identical whether or not a run is in progress. The
                # correlation registry is still updated above -- the sinkhole must
                # behave the same way too.
                idle += 1
                continue
            accepted.append(
                QueuedEvent(
                    run_id=run_id,
                    received_at=received_at,
                    type=event.type,
                    app=event.app,
                    ts=event.ts,
                    synthetic=synthetic,
                    payload=payload,
                )
            )

        if over_batch:
            log.error(
                "batch exceeded maxItems=%d, dropped %d trailing events",
                MAX_EVENTS_PER_BATCH,
                over_batch,
            )
        self.counters["received"] += len(accepted) + dropped + idle + over_batch
        self.counters["dropped_invalid"] += dropped
        self.counters["dropped_over_batch"] += over_batch
        self.counters["discarded_idle"] += idle

        queued = 0
        if accepted:
            try:
                self.queue.put_nowait(accepted)
                queued = len(accepted)
            except asyncio.QueueFull:
                self.counters["dropped_overflow"] += len(accepted)
                log.error("event queue full (maxsize=%d), dropped %d events", self.queue.maxsize, len(accepted))

        return {
            "accepted": queued,
            "dropped": dropped + over_batch,
            "discarded_idle": idle,
        }

    async def flush(self) -> None:
        """Block until everything queued so far has been written."""
        await self.queue.join()

    async def _writer_loop(self) -> None:
        while True:
            batch = await self.queue.get()
            items = list(batch)
            pending = 1
            # Opportunistic coalescing: under a scan the queue is rarely empty, and
            # one INSERT per HTTP request would make the database the bottleneck.
            while len(items) < self.settings.write_batch:
                try:
                    items.extend(self.queue.get_nowait())
                except asyncio.QueueEmpty:
                    break
                pending += 1
            try:
                await self._write(items)
            except Exception:
                self.counters["dropped_write_error"] += len(items)
                log.exception("failed to persist %d events", len(items))
            finally:
                for _ in range(pending):
                    self.queue.task_done()

    async def _write(self, items: list[QueuedEvent]) -> None:
        if not items:
            return
        assert self.session_factory is not None
        rows: list[dict[str, Any]] = []
        per_run: dict[str, int] = defaultdict(int)
        for item in items:
            seq = self._next_seq(item.run_id)
            rows.append(
                {
                    "run_id": item.run_id,
                    "seq": seq,
                    "type": item.type,
                    "app": item.app,
                    "ts": item.ts,
                    "synthetic": item.synthetic,
                    "received_at": item.received_at,
                    "payload": item.payload,
                }
            )
            per_run[item.run_id] += 1

        async with self.session_factory() as session:
            await session.execute(insert(Event), rows)
            for run_id, count in per_run.items():
                await session.execute(
                    update(Run).where(Run.run_id == run_id).values(event_count=Run.event_count + count)
                )
            await session.commit()
        self.counters["written"] += len(rows)

    def _next_seq(self, run_id: str) -> int:
        seq = self._seq.get(run_id, 0) + 1
        self._seq[run_id] = seq
        return seq

    async def get_events(
        self,
        run_id: str,
        *,
        event_type: str | None = None,
        after_seq: int | None = None,
        limit: int = 5000,
    ) -> dict[str, Any]:
        assert self.session_factory is not None
        await self.flush()
        stmt = select(Event).where(Event.run_id == run_id)
        if event_type is not None:
            stmt = stmt.where(Event.type == event_type)
        if after_seq is not None:
            stmt = stmt.where(Event.seq > after_seq)
        stmt = stmt.order_by(Event.seq).limit(limit)
        async with self.session_factory() as session:
            events = list((await session.execute(stmt)).scalars())
        return {
            "run_id": run_id,
            # Cursor to pass back as after_seq. Null means "nothing more right now",
            # which is also how a caller polling a live run detects a quiet moment.
            "next_seq": events[-1].seq if events else None,
            "events": [event.as_dict() for event in events],
        }

    async def stats(self) -> dict[str, Any]:
        assert self.session_factory is not None
        await self.flush()
        async with self.session_factory() as session:
            by_type = {
                row[0]: row[1]
                for row in (await session.execute(select(Event.type, func.count()).group_by(Event.type)))
            }
            total_runs = (await session.execute(select(func.count()).select_from(Run))).scalar() or 0
            synthetic = (
                await session.execute(select(func.count()).select_from(Event).where(Event.synthetic.is_(True)))
            ).scalar() or 0
        return {
            "active_run": self.active_run_id,
            "runs": int(total_runs),
            "events_by_type": by_type,
            "events_total": sum(by_type.values()),
            "synthetic_events": int(synthetic),
            "dropped": int(
                self.counters["dropped_invalid"]
                + self.counters["dropped_overflow"]
                + self.counters["dropped_over_batch"]
                + self.counters["dropped_write_error"]
            ),
            "dropped_detail": {
                "invalid": int(self.counters["dropped_invalid"]),
                "over_batch": int(self.counters["dropped_over_batch"]),
                "queue_overflow": int(self.counters["dropped_overflow"]),
                "write_error": int(self.counters["dropped_write_error"]),
            },
            "synthetic_by_source": int(self.counters["synthetic_by_source"]),
            "synthetic_cidrs": [str(net) for net in self.settings.synthetic_networks],
            "correlations": self.correlations.stats(),
            "discarded_idle": int(self.counters["discarded_idle"]),
            "received": int(self.counters["received"]),
            "written": int(self.counters["written"]),
            "queue_depth": self.queue.qsize(),
        }


def _preview(raw: Any, limit: int = 512) -> str:
    """Render an event for the drop log. Best-effort: this runs on an error path and
    must not raise a second time."""
    try:
        text = json.dumps(raw, default=str)
    except (TypeError, ValueError):
        text = repr(raw)
    return text[:limit]
