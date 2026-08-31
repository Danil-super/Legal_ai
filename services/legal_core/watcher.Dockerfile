FROM python:3.13.7-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/services/legal_core/src

WORKDIR /app

RUN groupadd --system app \
    && useradd --system --gid app --home-dir /app app \
    && mkdir -p /var/lib/dental-legal-ai/legal-update-inbox \
    && chown -R app:app /var/lib/dental-legal-ai

COPY requirements.lock ./
RUN python -m pip install --no-cache-dir --require-hashes -r requirements.lock

COPY services/legal_core/corpus/legal_watch_rules.v1.json ./services/legal_core/corpus/legal_watch_rules.v1.json
COPY services/legal_core/src ./services/legal_core/src

USER app

CMD ["python", "-m", "legal_core.legal_watcher", "--help"]
