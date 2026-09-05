# Out-of-band canary. Listens on five privileged ports, so the one interesting thing
# about this image is how it does that without running as root.
#
#   CAP_NET_BIND_SERVICE is granted as a *file* capability on the interpreter
#   (setcap below), which is why no change to docker-compose.yml is needed: the
#   container's default capability set already includes CAP_NET_BIND_SERVICE, and the
#   file capability lets the unprivileged `canary` user actually use it. Layers keep
#   the security.capability xattr, so this survives the build.
#
#   If a hardened deployment drops capabilities (`cap_drop: [ALL]`), give it back
#   explicitly in the compose service:
#
#       cap_add: ["NET_BIND_SERVICE"]
#
#   And if file capabilities are unavailable (a storage driver that eats xattrs, or a
#   userns-remapped daemon), the fallback is to move the listeners to unprivileged
#   ports instead of running as root:
#
#       environment:
#         BENCH_OOB_DNS_PORT: 5353
#         BENCH_OOB_HTTP_PORT: 8080
#         ...
#
# Everything else here is ordinary: one runtime dependency (cryptography, for the
# self-signed certificate), no build toolchain, no shell in the entrypoint.

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    BENCH_OOB_DOMAIN=oob.bench.local

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends libcap2-bin; \
    pip install --no-cache-dir .; \
    # Grant the bind capability to the interpreter that will run as `canary`.
    setcap 'cap_net_bind_service=+ep' "$(readlink -f "$(command -v python3)")"; \
    apt-get purge -y --auto-remove libcap2-bin; \
    rm -rf /var/lib/apt/lists/*; \
    useradd --system --no-create-home --uid 10001 canary; \
    # The TLS keypair is minted at startup into a temp dir, so /tmp must be writable
    # by the unprivileged user (it is, 1777, but be explicit about the expectation).
    chown -R root:root /app

USER 10001:10001

EXPOSE 53/udp 53/tcp 80/tcp 443/tcp 25/tcp 389/tcp 8901/tcp

HEALTHCHECK --interval=10s --timeout=5s --start-period=5s --retries=3 \
    CMD ["python", "-m", "bench_oob.healthcheck"]

ENTRYPOINT ["python", "-m", "bench_oob"]
