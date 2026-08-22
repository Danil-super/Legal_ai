FROM python:3.13.7-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/services/legal_core/src

WORKDIR /app

RUN groupadd --system app && useradd --system --gid app --home-dir /app app

COPY requirements.lock ./
COPY services/legal_core/src ./services/legal_core/src

RUN python -m pip install --no-cache-dir --require-hashes -r requirements.lock

USER app

EXPOSE 8000

CMD ["uvicorn", "legal_core.main:app", "--host", "0.0.0.0", "--port", "8000"]
