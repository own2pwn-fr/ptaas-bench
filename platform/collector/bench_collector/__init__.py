"""ptaas-bench collector service.

Stores ground-truth observations (HTTP requests seen by the instrumented targets,
oracle triggers, out-of-band canary hits) against exactly one active benchmark run.

The service is deliberately dumb: it records what the SDKs report and performs no
interpretation. Deciding whether an observation counts as reach/exercise/trigger is
the scoring engine's job, because the scorer is the only component allowed to read
the catalog -- keeping the answer key out of this process is part of the anti-cheat
guarantee (see tests/test_network_isolation.py).
"""

__version__ = "1.0.0"
