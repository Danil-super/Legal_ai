# ADR 0071: ограничить внутренние ожидания Hermes и сохранять безопасную причину отказа

## Статус

Согласовано для реализации и проверки. Выпуск требует независимого review и успешного CI.
Этот ADR не объявляет полный двухпроходный анализ работоспособным.

## Дата

2026-10-07

## Контекст

Изолированная проверка на вымышленных фактах после исправления response schema прошла
исследовательский контракт за 28,87 с, но reviewer завершился `HermesUnavailable` за 30,03 с.
Результат двух проходов — **неуспех**, а не доказательство качества юридического ответа.
Общий обработчик скрывал различие между HTTP-ошибкой и конкретным видом таймаута.

Read-only проверка Hermes 0.20.6, pinned commit
`5fc308a70719a83cccdbba4c0e39c23f5a8239d5`, установила:

- Внешний `HermesClient` имеет wall-clock предел 30 с и не повторяет запрос.
- Без явного provider config внутренний request timeout Hermes — 1800 с;
  non-stream stale threshold исследователя — 90 с, reviewer — 300 с из reasoning-model floor.
- `agent.api_max_retries` по умолчанию 3; значение 0 нормализуется в 1, а не отключает вызов.
  SDK retries уже равны 0 в primary/request client chokepoints.
- `CustomProfile` задаёт cap 65 536 токенов. Текущие публичные alias IDs не распознаются
  pinned predicate как требующие `max_completion_tokens`: chat adapter формирует `max_tokens`.
  Неизвестно, как reviewed provider учитывает скрытое reasoning этих aliases.
- Top-level `max_tokens` тела запроса к Hermes не используется API adapter.
  Config `model.max_tokens`, timeout и explicit reasoning effort проходят в upstream kwargs:
  это подтверждено pure builders pinned runtime, без inference и без создания `AIAgent`/сессии.
- Непотоковый API handler не передаёт `agent_ref` и не прерывает worker при закрытии клиентского
  соединения. `aiohttp.AppRunner` использует default `handler_cancellation=False`;
  отмена `run_in_executor` не останавливает уже работающий thread. SSE имеет отдельный interrupt.

## Решение

1. Добавить `HermesUnavailableReason` с закрытым набором `UNKNOWN`, `WALL_TIMEOUT`,
   `CONNECT_TIMEOUT`, `READ_TIMEOUT`, `WRITE_TIMEOUT`, `POOL_TIMEOUT`, `HTTP_STATUS`, `TRANSPORT`.
   Определять причину только по типу исключения. Не переносить сообщения, URL, HTTP body,
   заголовки или prompt в exception metadata/логи. Сохранять generic boundary message и
   подавление исходного exception context при преобразовании клиента.
2. Записывать одну структурированную operational запись с event, enum reason, entry point
   и существующим UUID idempotency/request ID. Не записывать patient/user ID, текст кейса,
   traceback или raw provider exception. Публичный ответ остаётся 503 с прежним
   `ANALYSIS_PROVIDER_UNAVAILABLE`, без нового detail.
3. Оба tool-free профиля используют `agent.api_max_retries: 1` и
   `providers.custom.request_timeout_seconds: 29`, `stale_timeout_seconds: 29`.
   Внешние 30 с, модели/provider/auth, cap и reasoning effort не меняются.
4. Дополнить существующий pinned preflight проверкой фактически разрешённых provider/model
   request/stale timeouts через `hermes_cli.timeouts`. Незаполненный, некорректный или
   расходящийся budget прекращает запуск без вывода конфигурационных значений.
   Diagnostic поля `apiAttemptsPerCycle`, `requestOperationTimeoutSeconds` и
   `configuredStaleTimeoutSeconds` обозначают настройку цикла, timeout HTTP операций и
   разрешённую конфигурационную базу stale соответственно; не общее число вызовов,
   wall-clock deadline или эффективный worker threshold.

## Границы и последствия

Это boundedness/correctness fix, **не измеренный performance win и не hard cancellation**:

- При transient transport failure pinned Hermes может восстановить primary transport и
  запустить ещё один attempt cycle даже при `api_max_retries: 1`. Убирается default cycle
  из трёх попыток, но не обещается единственный upstream вызов.
- Внутренний upstream streaming применяется и при непотоковом внешнем API ответе.
  Streaming stale detector (`agent/chat_completion_helpers.py`, pinned строки 5084–5167)
  масштабирует explicit базу для контекста больше 50k/100k estimated tokens до 240/300 с,
  затем безусловно применяет reasoning-model floor: у текущего reviewer даже малый контекст
  имеет effective stale 300 с при configured базе 29 с. Отдельный non-stream resolver
  (`run_agent.py`, `_compute_non_stream_stale_timeout`) масштабирует базу до 150/240 с;
  reasoning floor там не переопределяет explicit конфигурацию. Эти разные пути нельзя
  выдавать за одинаковый эффективный предел 29 с.
- Request timeout 29 реально передаётся HTTP SDK как timeout операций connect/read/write/pool.
  Непрерывные chunks сбрасывают read/stale ожидания; это не гарантирует полный wall-clock
  предел worker 29 с или его прекращение после внешних 30 с.
- Более короткий timeout может прервать корректный, но медленный provider ответ. Это допустимое
  fail-closed поведение: юридический ответ не выпускается при провале обязательного reviewer.
- Значение token cap нельзя выбирать наугад: hidden reasoning может исчерпать малый cap до JSON.
  Не добавлять `max_tokens` в Hermes API body как placebo и не менять alias/provider в этой правке.
- Полная стабильность двух проходов требует последующей bounded fictional проверки после review
  на стабильном deployment. Low-effort эксперимент не включает low в production автоматически
  и не доказывает юридическую корректность или approval источников.

## Проверка и откат

- TDD: разные mock HTTP timeout types/status, wall timeout, отсутствие raw exception/body/URL
  в публичном ответе и логе, сохранение caller cancellation и закрытия response stream.
- Guard tests: actual pinned timeout resolver contract, отсутствующий/неверный budget,
  bool/non-finite значения, неизменные empty toolsets и Compose mounts для обоих профилей.
- Runtime compatibility: process-local private config overrides и pure builders pinned image,
  без inference, persistent файлов, Legal Core/production DB writes.
- Полный pytest, Ruff, mypy, Compose и Code Graph после изменения. Не ослаблять CI.
- Откат — предыдущий repository commit/профиль через reviewed deployment; секреты не изменяются.
  Возврат старого профиля возвращает прежние длинные внутренние ожидания и потерю причины отказа.
