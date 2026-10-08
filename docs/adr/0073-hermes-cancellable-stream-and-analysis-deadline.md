# ADR 0073: отменяемый SSE-анализ и общий предел ожидания

## Статус

Согласовано для реализации и проверки. Выпуск — только после независимого review, CI и
обычной вымышленной проверки двух моделей. Не является подтверждением юридического качества.

## Дата

2026-10-07

## Контекст и доказательства

После ADR 0071 обычный research завершился за 16,60 с, а reviewer был отменён внешним
wall timeout за 30,03 с. Результат двух проходов — неуспех. Один дополнительно согласованный
reviewer-only probe на фиксированном вымышленном research завершился за 29,66 с, прошёл
существующий строгий контракт и вернул успешный finish. Это близость к прежнему пределу,
не измерение p95, не actual двухмодельный прогон и не юридически утверждённый эталон.

Pinned Hermes 0.20.6, commit `5fc308a70719a83cccdbba4c0e39c23f5a8239d5`:

- Nonstream API использует `run_in_executor` без `agent_ref`; отмена ожидания и закрытие
  клиентского соединения не останавливают работающий thread.
- SSE `_write_sse_chat_completion` передаёт `agent_ref`. При ошибке записи в закрытое
  соединение вызывает `request_hard_interrupt` и отменяет agent task. При отсутствии
  новых token chunks запись heartbeat производится каждые 30 секунд.
- Request operation timeout 29 с и configured stale base 29 с не являются общим deadline.
  Внутренний streaming может иметь reasoning floor 300 с (ADR 0071); HTTP chunks сбрасывают
  ожидание read. Поэтому увеличение stage wall без изменения пути отмены недостаточно.
- Worker уже имеет HTTP operation timeout 120 с, wall 125 с и fenced lease 180 с.

## Решение

1. Добавить opt-in `HermesEndpoint.stream_response`, default false для прежних клиентов.
   Runtime analysis использует SSE для обоих существующих профилей без изменения URL,
   API keys, provider, моделей, token cap, reasoning effort и внутренних timeout/retry настроек.
2. Research wall остаётся 30 с. Reviewer wall — 50 с: ограниченный запас для обычного
   проверяющего, чей корректный ответ наблюдался почти у прежней границы 30 с. Один общий
   `asyncio.timeout(115)` охватывает context → projection → оба прохода → submission.
   Этот budget общий, не сбрасывается между стадиями и не гарантирует каждой стадии все
   nominal operation budgets. Worker 120/125 и lease 180 не меняются.
3. Парсер принимает только ограниченный tool-free `chat.completion.chunk` stream:
   один choice index 0, только assistant role/text delta, успешный `stop`, затем `[DONE]`
   и корректный конец потока. EOF, error/length, tool events, некорректный UTF-8/JSON,
   duplicate keys, non-finite numbers, failed/partial metadata и избыточный размер — отказ.
   Предел raw envelope 1 MB и content 80k characters сохраняется до окончательного
   строгого JSON/schema/scope validation. Валидный JSON до EOF не считается успехом.
   Nonstream compatibility сохраняется для envelopes без finish field; явный неуспешный
   finish/metadata отклоняется и там.
4. Stage wall, whole-analysis wall и caller cancellation закрывают response через context
   manager. Caller cancellation не превращается в provider error. Whole-analysis deadline
   записывает только `analysis_deadline_exceeded`, entry point и UUID request ID; публичный
   ответ сохраняет 503 `ANALYSIS_PROVIDER_UNAVAILABLE`, без raw exception/body/PII.

## Границы

- SSE даёт существующему pinned серверу сигнал отключения, но **не мгновенный kill**.
  Распознавание может отставать до следующей записи, обычно до heartbeat 30 с; последующая
  остановка зависит от hard-interrupt обработки SDK/thread. Нельзя обещать total upstream
  wall 30/50/115 с или zero orphan work. Наш HTTP response/analysis ограничен независимо.
- Сам по себе `stop`/`[DONE]` доказывает только wire completion: stock pinned server
  скрывает `completed=False` без error text и некоторые `partial=True` за успешным finish.
  ADR 0074 отдельно исправляет это condition в памяти процесса с exact-source guard.
  Без этого дополнения нельзя обещать проверку скрытого worker status клиентским парсером.
- Нет автоматических повторов клиента и нет нового low-effort режима. Pinned transport
  recovery может сделать дополнительные upstream calls внутри существующей настройки.
- Таймаут на submission может произойти после commit Legal Core: idempotency и job fencing
  сохраняются; worker проверяет completed state перед повторным запуском, как прежде.
- Неподтверждённая/недействующая нормативная версия не становится evidence. HIGH/CRITICAL,
  tenant boundaries, legal verifier, source approval и запрет автоматического patient-send
  не меняются. Вымышленный contract pass не означает legal gold evaluation.

## Проверка и откат

- TDD: split UTF-8, heartbeat, строгий finish/EOF/schema/size, частичный валидный JSON,
  failed metadata, HTTP trickle, закрытие при wall/caller cancellation; прежний nonstream.
- Общий deadline прерывает context/reasoning/submission и не сбрасывается между ними;
  safe operational event, прежние auth и lease gates проходят без изменений.
- Full pytest, Ruff, mypy, Compose, Code Graph, независимый review. Затем один approved
  process-local ordinary research→review probe только с фиксированными вымышленными
  facts/evidence, без Legal Core writes и без вывода prompts/model bodies. Hermes может
  сохранить такой QA в собственной SQLite. Production readiness требует отдельного gate.
- Откат — предыдущий reviewed repository commit. Секреты/профили не меняются. Он возвращает
  nonstream path и прежний reviewer 30 с; отмена upstream снова менее управляемая.
