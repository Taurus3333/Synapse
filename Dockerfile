# Synapse API image. Run it beside Docker Compose Postgres and Redis.
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    SYNAPSE_ENV=prod \
    SYNAPSE_API_HOST=0.0.0.0 \
    SYNAPSE_API_PORT=8000

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --upgrade pip \
    && pip install .

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/health || exit 1

# Bind 0.0.0.0 inside the container. Database URL, Redis URL, and API keys come from the environment.
CMD ["synapse-api"]
