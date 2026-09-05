# wapiti has no published official image: upstream builds one in CI but never
# pushes it, and the community image (cyberwatch/wapiti) is amd64-only and predates
# 3.3.x. Building it here is the only way to benchmark a current version, and it has
# the side benefit that the version is pinned in the repository rather than in a
# floating tag.
#
#   docker build -t local/wapiti:3.3.2 runners/wapiti
#
# (the orchestrator does this for you when tools.yaml carries the build stanza)
FROM python:3.13-slim

ARG WAPITI_VERSION=3.3.2

# `wapiti-getcookie` and the module set need no system packages beyond certificates.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir "wapiti3==${WAPITI_VERSION}"

# Runs as a non-root uid so a compromised target cannot trivially own the bind mount.
# The run directory is chmod 0777 by the harness for exactly this reason.
RUN useradd --create-home --uid 1001 wapiti
USER wapiti
WORKDIR /work

ENTRYPOINT ["wapiti"]
