# ADR-0002: Tenant context устанавливается сервером

- Статус: принято ТЗ v0.1
- Дата: 2026-08-22

## Контекст

Кейсы и внутренние документы содержат чувствительные медицинские и персональные данные. Параметр `clinic_id`, полученный от клиента или LLM, нельзя считать доверенным.

## Решение

Tenant context выводится только из проверенной server-side identity/mapping и передаётся внутренним сервисам как обязательный контекст. Все tenant-owned таблицы имеют `clinic_id`; backend применяет scope на каждой операции, а PostgreSQL RLS добавляется как дополнительный уровень защиты. Cache keys и object-storage namespaces также tenant-scoped.

## Последствия

- Публичные контракты не принимают доверенный `clinic_id` для выбора tenant.
- Любой новый endpoint/tool с tenant data требует negative authz test.
- System administrator не получает доступ к кейсам по умолчанию.
