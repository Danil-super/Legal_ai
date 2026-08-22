# Spec: platform-bootstrap

## Objective

Создать воспроизводимый, безопасный каркас Dental Legal AI, на котором последующие модули смогут развиваться инкрементально. Первый срез предоставляет Legal Core API с публичными `live`/`ready` endpoint'ами и локальные PostgreSQL/pgvector, Redis и MinIO, не реализуя юридическую логику или LLM.

## Tech Stack

- Python 3.11+; FastAPI/Pydantic.
- PostgreSQL 16+ с pgvector, Redis, S3-compatible MinIO.
- pytest, Ruff, mypy, Docker Compose.
- SQLAlchemy 2.x и Alembic подключаются в первом срезе модели данных.

## Commands

```bash
python -m pip install -r requirements.lock
python -m pip install -e '.[dev]' --no-deps
python -m pytest
python -m ruff check .
python -m mypy services/legal_core/src
docker compose config --quiet
docker compose up --build
```

## Project Structure

- `services/legal_core/` — Legal Core REST API.
- `services/legal_mcp/`, `services/legal_updater/`, `services/gateway/telegram/`, `services/worker/` — будущие изолированные компоненты.
- `db/migrations/` — Alembic revisions.
- `docs/adr/` — решения, которые нельзя оставлять только в коде.
- `regression/cases/` — синтетические эталонные сценарии без реальных ПДн.
- `tests/integration/`, `tests/security/` — межсервисные и isolation-тесты.

## Code Style

```python
from typing import Final

SERVICE_NAME: Final = "legal-core"


def is_live() -> bool:
    return True
```

Используются type hints, короткие явные функции и Pydantic-схемы на внешних границах. Предметные состояния представлены enum, а не произвольными строками.

## Testing Strategy

- Small unit/contract tests выполняются без внешней сети.
- Integration tests используют контейнеры и проверяют реальные PostgreSQL/Redis/MinIO boundaries.
- Security tests обязаны включать cross-tenant denial до появления tenant-owned endpoint'ов.
- Новое поведение разрабатывается RED → GREEN → REFACTOR.

## Boundaries

- Always: health/readiness без auth; все остальные endpoint'ы с auth; конфигурация из environment; безопасные defaults; тесты и audit-friendly correlation IDs.
- Ask first: внешние интеграции, новые классы PII, auth flow, trusted sources, risk policy.
- Never: реальные patient fixtures; secret values в repo; LLM в deterministic updater; legal claims без evidence; автоматическая отправка draft пациенту.

## Success Criteria

- Репозиторий имеет документированные команды и стандартную структуру из ТЗ.
- `GET /health/live` возвращает стабильный typed contract и 200.
- `GET /health/ready` сообщает готовность зависимостей и не скрывает degraded state.
- Docker Compose валиден и описывает isolated local dependencies без реальных секретов.
- Unit tests, lint и typecheck проходят локально.

## Open Questions

- Какой конкретный release/commit Hermes закрепить после отдельного review upstream?
- Какой российский/локализованный LLM provider допустим после ПДн review?
- Какой production secret manager и S3 provider использовать?
- Кто утверждает trusted-source allowlist и исходные ≥100 regression cases?
