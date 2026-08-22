FROM python:3.13.7-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/services/legal_core/src

WORKDIR /app

RUN apt-get update \
    && apt-get install --yes --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system app \
    && useradd --system --gid app --home-dir /app app

COPY requirements.lock ./
COPY alembic.ini ./
COPY services/legal_core/corpus ./services/legal_core/corpus
COPY services/legal_core/migrations ./services/legal_core/migrations
COPY services/legal_core/src ./services/legal_core/src

RUN python -m pip install --no-cache-dir --require-hashes -r requirements.lock

USER app

EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && python -m legal_core.corpus_loader services/legal_core/corpus/initial_pp736.json && python -m legal_core.bootstrap_admin --if-configured && exec uvicorn legal_core.main:app --host 0.0.0.0 --port 8000"]
