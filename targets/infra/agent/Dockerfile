# The estate's telemetry agent. Built from the repository root so that the shared
# telemetry library is installed from source alongside it, which is how every other
# service on the estate consumes it.
FROM python:3.12-alpine

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /opt/agent

# The shared library first: it changes far less often than the agent does.
COPY sdk/python /opt/telemetry-agent
RUN pip install --no-cache-dir /opt/telemetry-agent "pymongo==4.10.1"

COPY targets/infra/agent/pyproject.toml ./
COPY targets/infra/agent/src ./src
RUN pip install --no-cache-dir .

# The same command the operator runs on the web host, so that an operator who is already
# on this one does not have to remember a second spelling.
COPY targets/infra/web/state-reset /usr/local/bin/state-reset
RUN chmod 0755 /usr/local/bin/state-reset

ENTRYPOINT ["python", "-m", "site_telemetry.main"]
