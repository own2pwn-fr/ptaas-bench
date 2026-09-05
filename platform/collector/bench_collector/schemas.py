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

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PARAM_LOCATIONS = Literal[
    "query", "body", "json", "path", "header", "cookie", "multipart", "raw", "graphql", "websocket"
]
ORACLE_KINDS = Literal["sink", "oob", "state", "differential", "timing", "artifact"]
OOB_CHANNELS = Literal["dns", "http", "https", "smtp", "ldap"]
EVENT_TYPES = Literal["http_request", "trigger", "oob", "note", "correlation"]

MAX_EVENTS_PER_BATCH = 500

VULN_ID_PATTERN = r"^BENCH-[A-Z0-9]+-[0-9]{4}$"
# Metric-shaped and opaque: `shop.catalog.query.plan_anomaly`. A planted sink emits
# this instead of a catalog id, so a tool that reads the compromised source finds
# something an ordinary application would plausibly have. The mapping back to a
# vulnerability lives in the catalog and is resolved by the scorer -- never here.
SIGNAL_PATTERN = r"^[a-z][a-z0-9]*(\.[a-z0-9_]+){2,}$"


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
    # The SDK may assert it, but it is no longer the only source: the collector also
    # marks an event synthetic when its client address falls inside the platform's
    # own CIDRs. See Collector._mark_synthetic.
    synthetic: bool = False
    # Address of the client that caused this event, on every type and not just on
    # http_request: it is what identifies platform traffic now that the selftest
    # header is gone (a header is visible to any reflection or header-injection flaw,
    # and would have handed the tool the shape of the grader).
    client_ip: str | None = None


class HttpRequestEvent(_EventBase):
    type: Literal["http_request"]
    method: str
    route: str
    path: str | None = None
    status: int | None = None
    auth_subject: str | None = None
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
    """A planted sink fired.

    Identified by an opaque ``signal`` in current targets, by ``vuln_id`` in older
    ones; at least one must be present or the event cannot be attributed at all and
    is dropped. The collector never resolves a signal to a vulnerability -- it does
    not read the catalog, and keeping the answer key out of this process is half the
    reason the network split exists.
    """

    type: Literal["trigger"]
    vuln_id: str | None = Field(default=None, pattern=VULN_ID_PATTERN)
    signal: str | None = Field(default=None, pattern=SIGNAL_PATTERN)
    oracle_kind: ORACLE_KINDS | None = None
    evidence: TriggerEvidence | None = None

    @model_validator(mode="after")
    def _needs_an_identifier(self) -> TriggerEvent:
        if not self.vuln_id and not self.signal:
            raise ValueError("a trigger must carry either signal or vuln_id")
        return self


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


class CorrelationBase(BaseModel):
    """A planted sink announcing an outbound fetch it is about to make.

    The sinkhole is the resolver for the whole target network, so it also captures
    callbacks aimed at the tool's own collaborator domain -- which is the point:
    without it every blind SSRF, XXE and command injection would score as missed by
    every tool, describing our topology rather than the tools. But a lookup for
    ``x.oast.fun`` carries nothing that ties it to a route or a parameter, so the
    sink registers the hint here and the sinkhole matches its observation against it.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    app: str
    signal: str = Field(pattern=SIGNAL_PATTERN)
    destination_host: str
    route: str | None = None
    param: str | None = None
    request_id: str | None = None
    ts: float | None = None
    synthetic: bool = False
    client_ip: str | None = None
    # Per-registration override; the deployment default is TELEMETRY_CORRELATION_TTL.
    ttl: float | None = Field(default=None, gt=0, le=3600)

    @field_validator("destination_host")
    @classmethod
    def _normalise_host(cls, value: str) -> str:
        # Hostnames are case-insensitive and DNS observations arrive with a trailing
        # dot; the sinkhole must not miss a match over either.
        return value.strip().rstrip(".").lower()


class CorrelationCreate(CorrelationBase):
    """Body of POST /v1/correlations."""


class CorrelationEvent(CorrelationBase):
    """The same record travelling through the event stream, so a published score can
    be audited: a reader sees which hint was live when the callback landed."""

    type: Literal["correlation"]
    correlation_id: str | None = None
    expires_at: float | None = None


AnyEvent = Annotated[
    HttpRequestEvent | TriggerEvent | OobEvent | NoteEvent | CorrelationEvent,
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
