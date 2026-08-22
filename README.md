# Dental Legal AI

Evidence-grounded AI assistant for legally significant situations in Russian dental clinics. The MVP uses Telegram as its user interface and keeps verified, versioned law in a separate Legal Core.

The project is in bootstrap development. It must not be used for real patient cases or production legal decisions until the legal, personal-data and medical-confidentiality reviews required by the specification are complete.

## Local development

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install -e '.[dev]' --no-deps
.venv/bin/python -m pytest
.venv/bin/python -m ruff check .
.venv/bin/python -m mypy services/legal_core/src services/gateway/telegram/src
```

Architecture and implementation order are documented in [CAPABILITY_MAP.md](CAPABILITY_MAP.md) and [tasks/plan.md](tasks/plan.md).

## Local containers

Copy `.env.example` to `.env`, replace both placeholder passwords, add the Telegram bot
token, then run:

```bash
docker compose up -d --build
curl --fail http://127.0.0.1:8000/health/live
curl --fail http://127.0.0.1:8000/health/ready
docker compose down
```

Apply the bot name, descriptions, commands and avatar as an explicit one-off operation:

```bash
docker compose run --rm telegram-gateway python -m telegram_gateway.profile
```

Telegram strictly rate-limits profile mutations, so they are deliberately not repeated on
every polling restart.

`docker compose down` keeps the named data volumes. Add `--volumes` only when intentionally deleting local PostgreSQL, Redis and MinIO data.

The Telegram gateway uses long polling and exposes no host port. `/start` and `/menu` open a
branded, image-based inline menu with project, workflow, preparation and privacy sections.
Until authentication and Case Core are implemented, free text receives a safety notice and is
not processed.
