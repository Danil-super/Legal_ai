FROM python:3.13.7-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/services/gateway/telegram/src:/app/services/legal_core/src

WORKDIR /app

RUN groupadd --system app && useradd --system --gid app --home-dir /app app

COPY requirements.lock ./
COPY services/gateway/telegram/src ./services/gateway/telegram/src
COPY services/legal_core/src/legal_core/__init__.py ./services/legal_core/src/legal_core/__init__.py
COPY services/legal_core/src/legal_core/pseudonymization.py ./services/legal_core/src/legal_core/pseudonymization.py

RUN python -m pip install --no-cache-dir --require-hashes -r requirements.lock

USER app

CMD ["python", "-m", "telegram_gateway"]
