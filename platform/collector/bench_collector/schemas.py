"""Pydantic mirrors of the schemas in openapi.yaml.

Two rules govern the strictness of these models, and they pull in opposite
directions:

* A structurally broken event is dropped (and loudly counted). Anything the scorer
  cannot attribute -- no ``app``, an unparsable ``vuln_id`` -- is worse than absent,
  because it would silently skew ground truth.
* An event that is merely *too big* is truncated, not dropped. Losing a trigger
  event because an SDK sent a 300-byte sample where the spec allows 256 would delete
  proof that a vulnerability was exploited; that is a far more expensive failure than
  a clipped string.

Unknown extra fields are preserved (``extra="allow"``) so an SDK that grows a field
before the collector knows about it does not lose evidence.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

PARAM_LOCATIONS = Literal[
    "query", "body", "json", "path", "header", "cookie", "multipart", "raw", "graphql", "websocket"
]
ORACLE_KINDS = Literal["sink", "oob", "state", "differential", "timing", "artifact"]
OOB_CHANNELS = Literal["dns", "http", "https", "smtp", "ldap"]
EVENT_TYPES = Literal["http_request", "trigger", "oob", "note"]

MAX_EVENTS_PER_BATCH = 500


def _truncator(limit: int):
    """Clip an over-long string instead of failing validation. See module docstring."""

    def _clip(value: str | None) -> str | None:
        if value is None or len(value) <= limit:
            return value
        return value[:limit]

    return _clip


class RunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str
    tool_version: str | None = None
    profile: str | None = None
    targets: list[str] = Field(default_factory=list)
    notes: str | None = None
    force: bool = False


class HttpParam(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    name: str
    # `in` is a Python keyword; the wire name is restored with by_alias on dump.
    in_: PARAM_LOCATIONS = Field(alias="in")
    value_sha256: str | None = None
    value_len: int | None = None
    sample: str | None = None

    _clip_sample = field_validator("sample")(_truncator(256))


class _EventBase(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    app: str
    ts: float | None = None
    synthetic: bool = False


class HttpRequestEvent(_EventBase):
    type: Literal["http_request"]
    method: str
    route: str
    path: str | None = None
    status: int | None = None
    auth_subject: str | None = None
    client_ip: str | None = None
    user_agent: str | None = None
    params: list[HttpParam] = Field(default_factory=list)


class TriggerEvidence(BaseModel):
    model_config = ConfigDict(extra="allow")

    payload: str | None = None
    detail: str | None = None
    request_id: str | None = None

    _clip_payload = field_validator("payload")(_truncator(1024))
    _clip_detail = field_validator("detail")(_truncator(1024))


class TriggerEvent(_EventBase):
    type: Literal["trigger"]
    vuln_id: str = Field(pattern=r"^BENCH-[A-Z0-9]+-[0-9]{4}$")
    oracle_kind: ORACLE_KINDS | None = None
    evidence: TriggerEvidence | None = None


class OobEvent(_EventBase):
    type: Literal["oob"]
    token: str
    channel: OOB_CHANNELS
    source_ip: str | None = None
    raw: str | None = None

    _clip_raw = field_validator("raw")(_truncator(2048))


class NoteEvent(_EventBase):
    type: Literal["note"]
    message: str | None = None


AnyEvent = Annotated[
    HttpRequestEvent | TriggerEvent | OobEvent | NoteEvent,
    Field(discriminator="type"),
]


class EventEnvelope(BaseModel):
    """Only used to document the request body in the generated schema.

    The endpoint itself validates event-by-event so that one malformed item cannot
    reject the whole batch -- see app.ingest_events.
    """

    model_config = ConfigDict(extra="allow")

    events: list[AnyEvent] = Field(default_factory=list, max_length=MAX_EVENTS_PER_BATCH)


def dump_event(event: BaseModel) -> dict[str, Any]:
    """Serialise a validated event back to its wire form (``in`` alias restored)."""
    return event.model_dump(mode="json", by_alias=True)
