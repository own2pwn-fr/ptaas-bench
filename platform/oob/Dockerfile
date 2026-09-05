# Internal edge resolver. It listens on five privileged ports, so the one interesting
# thing about this image is how it does that without running as root.
#
#   CAP_NET_BIND_SERVICE is granted as a *file* capability on the interpreter (setcap
#   below), which is why no change to the compose file is needed: the container's
#   default capability set already includes CAP_NET_BIND_SERVICE, and the file
#   capability lets the unprivileged user actually use it. Image layers keep the
#   security.capability xattr, so this survives the build.
#
#   If a hardened deployment drops capabilities (`cap_drop: [ALL]`), give it back:
#
#       cap_add: ["NET_BIND_SERVICE"]
#
#   And if file capabilities are unavailable (a storage driver that eats xattrs, or a
#   userns-remapped daemon), move the listeners to unprivileged ports rather than
#   running as root:
#
#       environment:
#         RESOLVER_DNS_PORT: 5353
#         RESOLVER_HTTP_PORT: 8080
#         ...
#
# Everything else is ordinary: one runtime dependency, no build toolchain, no shell in
# the entrypoint.

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends libcap2-bin; \
    pip install --no-cache-dir .; \
    setcap 'cap_net_bind_service=+ep' "$(readlink -f "$(command -v python3)")"; \
    apt-get purge -y --auto-remove libcap2-bin; \
    rm -rf /var/lib/apt/lists/*; \
    useradd --system --no-create-home --uid 10001 edge; \
    chown -R root:root /app

# TLS keys are minted at startup into a temporary directory, so /tmp must stay writable
# for the unprivileged user (it is, 1777; noted because it is a real requirement).
USER 10001:10001

EXPOSE 53/udp 53/tcp 80/tcp 443/tcp 25/tcp 389/tcp

HEALTHCHECK --interval=10s --timeout=5s --start-period=5s --retries=3 \
    CMD ["python", "-m", "edge_resolver.healthcheck"]

ENTRYPOINT ["python", "-m", "edge_resolver"]
