r"""Internal telemetry agent: request records, application signals, dependency links.

The agent turns every served request into one record (route template, status, timing
context, the inputs the handler could observe) and lets application code raise named
signals when it notices something worth counting. Records are exported to the
collector named by ``TELEMETRY_ENDPOINT`` over OTLP-style paths.

Wiring a FastAPI or Starlette service::

    from telemetry_agent import init_telemetry, TelemetryASGIMiddleware

    telemetry = init_telemetry()                 # TELEMETRY_SERVICE / TELEMETRY_ENDPOINT
    app.add_middleware(TelemetryASGIMiddleware, framework_app=app)

Wiring a Flask service::

    from telemetry_agent import init_telemetry, TelemetryWSGIMiddleware

    telemetry = init_telemetry()
    app.wsgi_app = TelemetryWSGIMiddleware(app.wsgi_app)

Raising a signal where the application notices an effect worth counting::

    telemetry.signal("shop.catalog.query.plan_anomaly",
                     {"payload": term, "detail": "row shape outside projection"})

Registering an outbound dependency call whose destination came from a request, so the
egress seen by the network can be tied back to the request that caused it::

    telemetry.outbound(url, signal="shop.imports.fetch.external", param="source_url")

Both are callable from anywhere in the request's call stack, however deep: the request
id, the observed peer and the classification of the traffic ride a ContextVar, which
follows ``await``, ``asyncio.to_thread`` and the framework thread pools. Work handed to
a bare ``ThreadPoolExecutor`` is the one exception, and ``telemetry.bind(fn)`` carries
the context into it.

Three properties this agent must not lose:

1. **No added latency, no failure propagation.** Recording appends to a bounded
   in-memory queue drained by a background thread. A collector that is down, slow or
   absent changes nothing observable in the service, including its response times.
2. **Nothing on the response path.** No response header, no extra route, no marker in
   an error body, no log line on the happy path. Clients, caches and captures of this
   service look the same whether the agent is loaded or not.
3. **The peer is what the socket said.** Every record carries ``peer_ip``, the address
   the middleware observed on the connection, and it is the only address anything
   downstream classifies traffic on. ``client_ip`` and the forwarded headers travel as
   description; an address a caller announced about itself never becomes ``peer_ip``.
4. **Route templates, not URLs.** ``/api/orders/{id}`` on Starlette,
   ``/api/orders/<int:id>`` on Flask, ``<unmatched>`` when nothing matched, with the
   concrete path kept alongside.

Signal names are metric names -- lower case, at least three dot-separated segments --
validated against ``^[a-z][a-z0-9]*(\.[a-z0-9_]+){2,}$``. A name that does not match is
counted and dropped rather than raised.

Environment: ``TELEMETRY_SERVICE``, ``TELEMETRY_ENDPOINT``, ``TELEMETRY_ENABLED``,
``TELEMETRY_SYNTHETIC_CIDRS`` (peer networks whose traffic is generated rather than
organic -- uptime probes, warm-up jobs -- decided on the socket peer address and never
on a forwarded header), ``TELEMETRY_EVENTS_PATH`` (default ``/v1/traces``),
``TELEMETRY_CORRELATIONS_PATH`` (default ``/v1/correlations``), ``TELEMETRY_QUEUE_MAX``,
``TELEMETRY_BATCH_MAX``, ``TELEMETRY_FLUSH_INTERVAL_MS``, ``TELEMETRY_TIMEOUT_S``,
``TELEMETRY_MAX_BODY_BYTES``, ``TELEMETRY_MAX_PARAMS``.
"""

from ._client import (
    TelemetryClient,
    get_telemetry,
    init_telemetry,
    note,
    outbound,
    signal,
)
from ._config import TelemetryConfig, config_from_env
from ._params import describe_param, flatten_json, sha256_of
from .asgi import TelemetryASGIMiddleware
from .wsgi import TelemetryWSGIMiddleware

__version__ = "1.0.0"

__all__ = [
    "TelemetryASGIMiddleware",
    "TelemetryClient",
    "TelemetryConfig",
    "TelemetryWSGIMiddleware",
    "config_from_env",
    "describe_param",
    "flatten_json",
    "get_telemetry",
    "init_telemetry",
    "note",
    "outbound",
    "sha256_of",
    "signal",
    "__version__",
]
