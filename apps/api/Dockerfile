FROM ghcr.io/astral-sh/uv:0.11.3 AS uv

FROM python:3.13-slim

LABEL org.opencontainers.image.title="AgentScope API" \
      org.opencontainers.image.description="AgentScope API, worker, and deployment tools"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

RUN apt-get update \
    && apt-get install --no-install-recommends --yes git \
    && rm -rf /var/lib/apt/lists/*

COPY --from=uv /uv /uvx /bin/
COPY apps/api/pyproject.toml apps/api/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project

COPY apps/api/src ./src
COPY apps/api/scripts ./scripts
COPY apps/api/alembic.ini ./
COPY apps/api/alembic ./alembic
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev

RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000
CMD ["uvicorn", "agentscope_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
