# --- build: resolve and install dependencies with uv, from the lockfile ------------------
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS build
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0
WORKDIR /app

# Dependencies first, so this layer is cached until the lockfile changes.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY README.md LICENSE ./
COPY src ./src
RUN uv sync --locked --no-dev --no-editable

# --- runtime: just Python and the virtualenv, as a non-root user ------------------------
FROM python:3.12-slim-bookworm
RUN useradd --system --uid 10001 --no-create-home app
COPY --from=build /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    MCP_TRANSPORT=http \
    HOST=0.0.0.0 \
    PORT=8080
USER app
EXPOSE 8080
CMD ["ask-bigquery"]
