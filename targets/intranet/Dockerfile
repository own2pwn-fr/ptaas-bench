# Staff services. Single stage: the application is a handful of Python files and the
# only build step is resolving the pinned dependencies.
FROM python:3.12.7-slim-bookworm

# The equipment screen sends one echo request when somebody asks whether a switch is
# on the network; without this the button can only ever go amber.
RUN apt-get update \
 && apt-get install -y --no-install-recommends iputils-ping \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    HUB_ROOT=/srv \
    HUB_DATABASE=/var/lib/hub/hub.sqlite3 \
    HUB_LOG_DIR=/var/lib/hub/log \
    PORT=80

WORKDIR /srv

COPY requirements.txt /srv/requirements.txt
RUN pip install --no-cache-dir -r /srv/requirements.txt

# The platform team's observability agent, installed from the internal package tree
# the build is given rather than from the index.
COPY --from=agent . /tmp/agent
RUN pip install --no-cache-dir /tmp/agent && rm -rf /tmp/agent

COPY hub /srv/hub
COPY bin/state-reset /usr/local/bin/state-reset
COPY bin/start /usr/local/bin/start
RUN chmod 0755 /usr/local/bin/state-reset /usr/local/bin/start

RUN useradd --system --uid 10001 --create-home --home-dir /home/app app \
 && mkdir -p /var/lib/hub/log \
 && chown -R app:app /var/lib/hub

# The listener is on 80, so the master starts privileged and hands every worker to the
# service account before it serves a single request: the processes that run application
# code, and the processes the equipment probe forks, are all `app`. The database and the
# logs belong to `app` too, and the reset command keeps them that way when an engineer
# runs it as root.
EXPOSE 80
ENTRYPOINT ["/usr/local/bin/start"]
