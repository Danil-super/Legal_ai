# Dental Legal AI: правила разработки

## Источник требований

- Основная спецификация: `Dental_Legal_AI_TZ_v0.1.docx` (версия 0.1 от 22.08.2026).
- Карта границ и порядка реализации: `CAPABILITY_MAP.md`.
- Текущий план: `tasks/plan.md`; исполняемые задачи: `tasks/todo.md`.
- Legal Core, а не LLM и не Hermes memory, является источником юридической истины.

## Обязательные правила

- Новая таблица сопровождается Alembic migration.
- Новый MCP tool сопровождается схемой контракта и authz-тестами.
- LLM нельзя добавлять в deterministic fetch/versioning layer.
- Изменение risk policy создаёт новую версию policy.
- В fixtures и логах запрещены реальные данные пациентов и raw medical documents.
- Legal claims из prompt/model memory не заменяют retrieved evidence.
- Критичный контракт меняется только вместе с тестами и ADR.
- Любая tenant-owned запись имеет `clinic_id`; tenant context устанавливается сервером.
- Production retrieval использует только `APPROVED` версии и учитывает дату действия.
- Автоматическая отправка юридически значимого ответа пациенту запрещена в MVP.

## Команды качества

После bootstrap использовать команды проекта:

```bash
python -m pytest
python -m ruff check .
python -m mypy services/legal_core/src
docker compose config --quiet
```

## Границы полномочий

- Всегда: валидировать внешний ввод, проверять tenant scope, вести audit без raw PII, писать тесты на новое поведение.
- Согласовать: новые категории персональных данных, внешние сервисы/LLM, изменения auth/CORS/rate limits, trusted sources, risk/escalation policy.
- Никогда: коммитить секреты, угадывать право без evidence, автоматически признавать ответственность, автоматически approve нормативную версию.
