# skipfish has no official image and every community one is stale and amd64-only,
# but it is still packaged in Kali (2.10b-2kali8, accepted 2025-12), and the Kali
# base is multi-arch. Building from the distribution package rather than from a
# random image means the provenance of the binary is an auditable packaging trail.
#
#   docker build -t ptaas-bench/skipfish:2.10b runners/skipfish
#
# (the orchestrator does this for you when tools.yaml carries the build stanza)
FROM kalilinux/kali-rolling

RUN apt-get update \
 && apt-get install -y --no-install-recommends skipfish ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Pin the exact package version into the image so `dpkg -s skipfish` in the run
# record is meaningful.
RUN dpkg -s skipfish | grep '^Version:' > /skipfish.version

WORKDIR /bench

# No ENTRYPOINT: the driver needs a shell to copy the wordlist before scanning
# (skipfish rewrites its dictionary in place, so each run must get its own copy).
