FROM --platform=linux/amd64 python:3.14.7-slim-bookworm@sha256:23c59390fc717bf09f9336908199a0ae75d9c4264bf296123f94ad772fea3b52

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:${PATH}"

WORKDIR /app

RUN python -m pip install --no-cache-dir uv==0.11.22

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY scripts/serve_orchestrator.py ./scripts/serve_orchestrator.py

RUN uv sync --frozen --no-dev \
    && groupadd --system agentiq \
    && useradd --system --create-home --gid agentiq agentiq

USER agentiq

EXPOSE 8080

ENTRYPOINT ["python", "scripts/serve_orchestrator.py", "--port", "8080"]
