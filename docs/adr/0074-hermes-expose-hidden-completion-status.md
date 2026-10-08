# ADR 0074: не скрывать незавершённый worker за успешным SSE finish

## Статус

Согласовано для реализации и проверки; требуется независимый review и успешный CI.
Не разрешает live patch, изменение моделей/provider/auth или юридическое approval.

## Дата

2026-10-07

## Контекст

ADR 0073 ограничивает ожидание и проверяет завершённый wire stream. Read-only анализ pinned
Hermes 0.20.6, commit `5fc308a70719a83cccdbba4c0e39c23f5a8239d5`, выявил скрытый status gap
в `gateway/platforms/api_server.py`, `APIServerAdapter._write_sse_chat_completion` (5402–5594):

- 5525–5530: `completed=False` вызывает error finish только вместе с error text;
  `partial=True` вызывает length только при truncation text. В иных случаях выбирается stop.
- 5543–5556: `hermes` metadata выдаётся только при finish, отличном от stop. Поэтому такой
  скрытый failure клиент не может обнаружить по успешному wire finish, даже строго читая JSON.
- Nonstream envelope сохраняет flags/`X-Hermes-Completed` при любом partial/failed/incomplete
  result. Недостаток относится к SSE finish decision, не требует смены provider или reasoning.

Один ordinary process-local fictional two-model probe прошёл wire/reasoning contract за 42,47 с
(research 14,87; review 27,50). Это не доказывает отсутствие скрытого status gap или legal gold.

## Решение

1. Добавить read-only mounted `run_guarded_gateway.py`. После прежнего render/tool/budget
   preflight он импортирует pinned adapter, проверяет SHA-256 **всего dedented SSE handler**:
   `55a06de31557760a11ad5e2576ddde96ae0ca7e21e3d1c991886616a85826abf`, и единственную
   ожидаемую condition. Drift/import/compile failure прекращает запуск с constant error,
   без private config/source/body/traceback.
2. В памяти процесса заменить только error condition на
   `agent_error is not None or is_failed or is_partial or not completed`.
   Truncation → length сохраняется. Остальные incomplete/partial → error, поэтому существующий
   metadata branch больше не скрывает flags и клиент отказывает fail-closed. Normal successful
   result, defaults pinned `completed` resolver, usage, disconnect handling и callbacks сохраняются.
3. Выполнить тот же установленный CLI через `runpy` в этом интерпретаторе, с прежними
   `gateway run --no-supervise`. Subprocess/exec CLI запрещён здесь: потерял бы memory patch.
   Upstream source/image и persistent config не переписываются. Нет новой CLI/config поверхности.
4. В обеих Compose entrypoints заменить только запуск CLI на launcher и добавить его read-only
   mount. Профили/ключи/модели/effort/budget/toolsets, networks, capability restrictions не меняются.
   Изменённые entrypoints требуют reviewed recreation обоих Hermes после CI; не применять патч
   вручную к живому сервису и не выдавать bind mount за уже активный launcher.

## Проверка и границы

- TDD воспроизводит `completed=False` без error, `partial=True` без error/non-truncation error,
  failed, ordinary success и прежний truncation; actual strict stream client отклоняет failures.
- Negative hash/условие drift, безопасное сообщение, process-only installation, сохранение
  unrelated adapter method, точный CLI/args, обе read-only mounts и прежние startup preflights.
- Pure actual pinned compatibility проверяется без создания agent, model calls, patient text,
  session/Legal Core DB writes. Full pytest/Ruff/mypy/Compose/Code Graph и independent review.
- SHA guard не утверждает юридическую корректность результата и не заменяет Legal Core
  APPROVED/date/scope/risk/verifier gates. Если worker сам неправильно ставит completed, эта
  правка не может восстановить неизвестный смысл: она устраняет доказанное сокрытие flags.
- Откат — прежний launcher/Compose через reviewed release; вернёт stock скрытый SSE status gap.
