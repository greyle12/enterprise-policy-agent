# syntax=docker/dockerfile:1

FROM python:3.12.10-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

RUN python -m venv /opt/venv

COPY pyproject.toml README.md ./
COPY app ./app

RUN /opt/venv/bin/pip install --index-url https://download.pytorch.org/whl/cpu torch \
    && /opt/venv/bin/python -m pip install .


FROM python:3.12.10-slim AS runtime

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_HOME=/app/data/model-cache/huggingface \
    SENTENCE_TRANSFORMERS_HOME=/app/data/model-cache/sentence-transformers \
    TORCH_HOME=/app/data/model-cache/torch \
    XDG_CACHE_HOME=/app/data/model-cache \
    SQLITE_DATABASE_PATH=/app/data/runtime/enterprise_policy_agent.db

WORKDIR /app

RUN apt-get update \
    && apt-get install --yes --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 agent \
    && useradd \
        --uid 10001 \
        --gid 10001 \
        --create-home \
        --home-dir /home/agent \
        --shell /usr/sbin/nologin \
        agent \
    && mkdir -p /app/data/runtime /app/data/model-cache /app/scripts \
    && chown -R agent:agent /app /home/agent

COPY --from=builder /opt/venv /opt/venv
COPY --chown=agent:agent app ./app
COPY --chown=agent:agent data/policies ./data/policies
COPY --chown=agent:agent scripts/check_container_health.py ./scripts/
COPY --chown=agent:agent scripts/container_persistence_probe.py ./scripts/

USER agent

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=10m --retries=10 \
    CMD ["python", "-m", "scripts.check_container_health", "--url", "http://127.0.0.1:8000/health/ready", "--timeout-seconds", "4"]

STOPSIGNAL SIGTERM

CMD ["python", "-m", "app"]
