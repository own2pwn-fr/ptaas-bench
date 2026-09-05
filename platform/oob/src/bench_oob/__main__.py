"""Entry point: ``python -m bench_oob`` (and the ``bench-oob`` console script).

Configuration is entirely environmental; see bench_oob.config. In the compose stack the
only two variables that matter are BENCH_OOB_DOMAIN and BENCH_COLLECTOR_URL.
"""

from __future__ import annotations

import logging
import os

from .config import Config
from .service import OobService


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("BENCH_OOB_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    OobService(Config.from_env()).run()


if __name__ == "__main__":
    main()
