FROM --platform=linux/amd64 python:3.14.6-slim-bookworm@sha256:86f975aca15cf04a40b399eebede9aea7c82eae084d1f1a0a6ef6bcaae871a30

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:${PATH}" \
    PORT=8088

WORKDIR /app

RUN python -m pip install --no-cache-dir uv==0.11.22

COPY pyproject.toml uv.lock README.md ./
COPY src ./src

RUN uv sync --frozen --no-dev --group foundry \
    && groupadd --system agentiq \
    && useradd --system --create-home --gid agentiq agentiq

USER agentiq

EXPOSE 8088

ENTRYPOINT ["python", "-m", "agent_iq.runtime.host_bootstrap"]
