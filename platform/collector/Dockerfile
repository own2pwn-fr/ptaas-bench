# Single stage on purpose: the dependency set is pure-python wheels, so a builder
# stage would add complexity without shrinking anything meaningful.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependency layer first so that editing collector code does not re-resolve wheels.
COPY pyproject.toml ./
COPY bench_collector ./bench_collector
RUN pip install --no-cache-dir . \
    && find /usr/local -name '__pycache__' -type d -prune -exec rm -rf {} + \
    && useradd --system --uid 10001 --no-create-home bench

USER 10001
EXPOSE 8900

# Single worker: seq allocation and the active-run pointer live in process memory,
# so a second worker would hand out duplicate sequence numbers.
CMD ["uvicorn", "bench_collector.app:app", "--host", "0.0.0.0", "--port", "8900", "--workers", "1"]
