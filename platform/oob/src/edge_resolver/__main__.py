"""Entry point: ``python -m edge_resolver``.

Configuration is entirely environmental; see edge_resolver.config. In the deployed stack
the two variables that matter are TELEMETRY_ENDPOINT and SINKHOLE_ZONE.
"""

from __future__ import annotations

import logging
import os

from .config import Config
from .service import ResolverService


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("RESOLVER_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    ResolverService(Config.from_env()).run()


if __name__ == "__main__":
    main()
