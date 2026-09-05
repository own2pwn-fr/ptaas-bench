"""Address helpers: which of our addresses does a given peer see us on?"""

from __future__ import annotations

import socket


def local_address_towards(host: str, port: int = 53, default: str | None = None) -> str | None:
    """Local source address the kernel would use to reach ``host``.

    A connected UDP socket sends nothing, it only pins a route, so this is a cheap way
    for a dual-homed host to answer "which of my addresses faces this peer?" without
    hardcoding subnets. Used for two things: the A record we hand back (the client must
    be able to reach the address it resolves) and the interface the admin API binds to
    (the one facing the reporting endpoint, i.e. the internal network)."""
    if not host:
        return default
    for family in (socket.AF_INET, socket.AF_INET6):
        try:
            sock = socket.socket(family, socket.SOCK_DGRAM)
        except OSError:
            continue
        try:
            sock.connect((host, port))
            address = sock.getsockname()[0]
            if address and not address.startswith("0."):
                return address
        except OSError:
            continue
        finally:
            sock.close()
    return default
