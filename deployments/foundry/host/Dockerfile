FROM --platform=linux/amd64 python:3.13.5-slim-bookworm@sha256:a7cb177dc60dfa77b223192cd0d9e6ce1aac7cf9a569023d68e7c91b6371a718

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
