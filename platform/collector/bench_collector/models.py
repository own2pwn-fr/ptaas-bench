"""Database schema.

Two tables only. There is no migration tooling on purpose: a benchmark run is
disposable, the database is recreated with the stack, and an ops-free schema keeps
the collector image tiny.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# JSONB in production for indexable evidence queries, plain JSON on SQLite in tests.
JSONType = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    pass


class Run(Base):
    __tablename__ = "runs"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tool: Mapped[str] = mapped_column(String(128))
    tool_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    profile: Mapped[str | None] = mapped_column(String(128), nullable=True)
    targets: Mapped[list[str]] = mapped_column(JSONType, default=list)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Denormalised so that listing runs never pays for a COUNT(*) over an event
    # table that reaches millions of rows during a full scan.
    event_count: Mapped[int] = mapped_column(Integer, default=0)

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "tool": self.tool,
            "tool_version": self.tool_version,
            "profile": self.profile,
            "targets": list(self.targets or []),
            "notes": self.notes,
            "started_at": _iso(self.started_at),
            "closed_at": _iso(self.closed_at),
            "active": bool(self.active),
            "event_count": int(self.event_count or 0),
        }


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        # The scorer exports incrementally with ?after_seq=, so (run_id, seq) is both
        # the pagination cursor and the uniqueness guarantee.
        UniqueConstraint("run_id", "seq", name="uq_events_run_seq"),
        Index("ix_events_run_type_seq", "run_id", "type", "seq"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), ForeignKey("runs.run_id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(32))
    app: Mapped[str] = mapped_column(String(128))
    # Target-supplied clock. Kept beside received_at because a target's clock may
    # drift, and an oracle argument sometimes hinges on ordering at the target.
    ts: Mapped[float | None] = mapped_column(Float, nullable=True)
    synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONType)

    def as_dict(self) -> dict[str, Any]:
        # The export contract is "the submitted payload plus seq/received_at", so the
        # payload is spread at the top level rather than nested under a key.
        out = dict(self.payload or {})
        out["seq"] = self.seq
        out["received_at"] = _iso(self.received_at)
        return out


def _iso(value: datetime | None) -> str | None:
    """Render a timestamp as UTC ISO-8601.

    SQLite has no timezone-aware storage and hands back naive datetimes, so the UTC
    marker is re-attached here; otherwise the same event would serialise differently
    depending on the backend and break the scorer's parsing across environments.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()
