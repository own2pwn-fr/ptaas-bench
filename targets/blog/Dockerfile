# Build context is the repository root: the service and the estate's observability
# agent are versioned together and the image is built from both.
FROM python:3.12-slim-bookworm AS build
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY targets/blog/requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt
# The agent ships as a distribution, not as a vendored directory, so a service can be
# rebuilt against a newer one without a patch to the service.
COPY sdk/python/pyproject.toml /tmp/agent/pyproject.toml
COPY sdk/python/src /tmp/agent/src
RUN pip install /tmp/agent

FROM python:3.12-slim-bookworm
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
RUN useradd --system --uid 10001 --create-home --home-dir /home/press press \
 && mkdir -p /var/lib/pressroom/media \
 && chown -R press:press /var/lib/pressroom
COPY --from=build /opt/venv /opt/venv
WORKDIR /srv/pressroom
COPY targets/blog/pressroom ./pressroom
COPY targets/blog/web ./web
COPY targets/blog/state-reset /usr/local/bin/state-reset
COPY targets/blog/serve /usr/local/bin/serve
RUN chmod 0755 /usr/local/bin/state-reset /usr/local/bin/serve
USER press
EXPOSE 8000
CMD ["/usr/local/bin/serve"]
