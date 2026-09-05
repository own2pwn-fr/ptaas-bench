"""benchctl -- the scoring brain of ptaas-bench.

This package is the only component that reads the ground-truth catalog. Everything
else in the platform (collector, SDKs, runners) stays deliberately dumb and reports
what happened on the wire; interpretation lives here, so the definition of a score
can be reviewed, contested and re-run against archived events without touching the
targets.

Layout:
    routes    route-template normalisation across five framework dialects
    catalog   YAML + JSON-Schema loading, class-default resolution, integrity checks
    events    collector event stream typing and loading
    scoring   reach / exercise / trigger per vulnerability, plus aggregation
    findings  false-positive classification and precision
    report    Markdown / self-contained HTML / raw JSON rendering
    cli       the `bench` command
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
