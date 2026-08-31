FROM python:3.13.7-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/services/legal_core/src

WORKDIR /app

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        fonts-dejavu-core \
        poppler-utils \
        tesseract-ocr \
        tesseract-ocr-rus \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system app \
    && useradd --system --gid app --home-dir /app app

COPY requirements.lock ./
RUN python -m pip install --no-cache-dir --require-hashes -r requirements.lock

COPY alembic.ini ./
COPY services/legal_core/corpus ./services/legal_core/corpus
COPY services/legal_core/migrations ./services/legal_core/migrations
COPY services/legal_core/src ./services/legal_core/src

USER app

EXPOSE 8000

CMD ["sh", "-c", "python -m legal_core.runtime_db_role && alembic upgrade head && python -m legal_core.runtime_db_role && python -m legal_core.corpus_loader services/legal_core/corpus/initial_pp736.json && python -m legal_core.corpus_loader services/legal_core/corpus/initial_pp659.json && python -m legal_core.corpus_loader services/legal_core/corpus/official/pp736-a7de08b6176991ed.json && python -m legal_core.corpus_loader services/legal_core/corpus/official/pp659-7af320a12723f96f.json && python -m legal_core.corpus_loader services/legal_core/corpus/official/pp659-substantive-selection.json && python -m legal_core.bootstrap_admin --if-configured && exec uvicorn legal_core.main:app --host 0.0.0.0 --port 8000"]
