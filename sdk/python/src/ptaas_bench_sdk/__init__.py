"""ptaas-bench instrumentation SDK for Python targets.

A deliberately vulnerable benchmark app imports this library to report *ground truth*:
which route was reached, which inputs the handler could observe, and which planted
sink actually fired. The scoring engine compares that ground truth with what the tool
under test claims to have found.

Wiring a target (FastAPI/Starlette)::

    from ptaas_bench_sdk import init_bench, BenchASGIMiddleware

    bench = init_bench()                       # BENCH_APP / BENCH_COLLECTOR_URL
    app.add_middleware(BenchASGIMiddleware, framework_app=app)

Wiring a target (Flask)::

    from ptaas_bench_sdk import init_bench, BenchWSGIMiddleware

    bench = init_bench()
    app.wsgi_app = BenchWSGIMiddleware(app.wsgi_app)

Reporting a planted sink, inside the vulnerable code path::

    bench.trigger("BENCH-SHOP-0001", oracle_kind="sink", payload=sql,
                  detail="UNION reached the parser")

Three properties this library must never lose, because the benchmark's validity rests
on them:

1. **No added latency and no failure propagation.** Emission is an append to a bounded
   in-memory queue drained by a background thread; a collector that is down, slow or
   missing changes nothing observable in the target. Timing-based oracles depend on it.
2. **No self-disclosure.** No response header, no extra route, no log line, no marker
   in an error body. The tool under test must not be able to tell an instrumented app
   from an uninstrumented one.
3. **Route templates, not URLs.** Events carry ``/api/orders/{id}`` (Starlette) or
   ``/api/orders/<int:id>`` (Flask) as registered by the framework, with
   ``<unmatched>`` when nothing matched and the concrete path always in ``path``.
"""

from ._client import BenchClient, get_bench, init_bench, note, trigger
from ._config import BenchConfig, config_from_env
from ._params import describe_param, flatten_json, sha256_of
from .asgi import BenchASGIMiddleware
from .wsgi import BenchWSGIMiddleware

__version__ = "1.0.0"

__all__ = [
    "BenchASGIMiddleware",
    "BenchClient",
    "BenchConfig",
    "BenchWSGIMiddleware",
    "config_from_env",
    "describe_param",
    "flatten_json",
    "get_bench",
    "init_bench",
    "note",
    "sha256_of",
    "trigger",
    "__version__",
]
