FROM python:3.13.7-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/services/agent_orchestrator/src:/app/services/legal_core/src

WORKDIR /app

RUN groupadd --system app && useradd --system --gid app --home-dir /app app

COPY requirements.lock ./
RUN python -m pip install --no-cache-dir --require-hashes -r requirements.lock

COPY services/legal_core/src ./services/legal_core/src
COPY services/agent_orchestrator/src ./services/agent_orchestrator/src

USER app

EXPOSE 8010

CMD ["uvicorn", "agent_orchestrator.main:create_runtime_app", "--factory", "--host", "0.0.0.0", "--port", "8010"]
