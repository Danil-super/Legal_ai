# ADR 0082: единая V2-анкета и понятное представление отчёта

## Контекст и дата

2026-10-13. Владелец утвердил SPEC-report-action-plan.md и поручил проверить
сценарии, исправить расхождение веток. Main 0b0a882 имеет успешный контрольный
V2-анализ, но быстрый production receiver создаёт V1; заполненное уточнение
Core поддерживает только V2. Пройденные mock-тесты старого quick flow не
доказывают единый сценарий или отсутствие ошибок у реальных пользователей.

## Решение и границы

- Новое быстрое описание сохраняет только обезличенный, проверенный тем же
  V2 parser текст EVENT_SUMMARY после проверки пользователем. POST явно
  выбирает dental-case-intake.v2. Отсутствующие сведения не угадываются и не
  превращаются в NO. Затем обычная V2-анкета задаёт недостающие вопросы,
  требует safety и подтверждённую фабулу, условно запрашивает услуги.
- До сообщения об успешном сохранении проверяются созданный V2 draft marker,
  состояние, ID, revision и точное совпадение сохранённых confirmed данных.
  Неоднозначный ответ использует те же POST/PUT ключи. Эти pending ключи
  существуют только в памяти до рестарта; после рестарта восстановление —
  из server-owned списка черновиков, не обещание постоянства pending nonce.
- Исправление описания отменяет предыдущую несохранённую карточку; новая
  карточка получает новый nonce. Старые кнопки не меняют новую карточку.
- Существующие V1 черновики и sparse legacy handlers сохраняются; никакой
  незаметной конверсии старых фактов, дат, отчётов и источников. Для V1
  CLARIFICATION_SCHEMA_UNSUPPORTED — понятный новый V2 case:start, не
  бесконечное повторение неработающего уточнения. V2 correctionSource,
  ACL, idempotency, исходные материалы и история не меняются.
- Telegram и PDF представляют только сохранённый CanonicalReport в шести
  одобренных блоках. Технические tokens/UUID/hashes остаются в хранилище,
  но не в основном тексте. Неизвестность, blocked состояние, необходимость
  юриста и ограничения текущих юридических копий остаются видимыми.
  Неподтверждённые инструкции модели не становятся планом. Общая внутренняя
  проверка не обозначается как детальная юридическая стратегия.

Нет нового schema/API, таблиц, миграции, trusted source, личных категорий данных,
auth/rate policy, модели или провайдера. Core verifier, policy v4 10 000 ₽,
approval/effective-date gates и запрет автоматического ответа пациенту прежние.
Исторические сохранённые PDF не перезаписываются из-за нового renderer.

## Проверка и выпуск

Независимые RED сценарии на production composition: 14 failed / 14 passed;
контроль manual V2 прошёл. Main RED ещё три отказа по quick schema/неизвестности
и V1 clarification. После исправления — 75 focused passed, включая два
несовпадающих server-response сценария. Существующие pure V1 tests не удалены;
две устаревшие production quick expectations изменены согласно новому требованию,
fake Core теперь применяет настоящие create/update контракты вместо игнорирования POST.

Независимое review границ и diff не обнаружило actionable ошибок; это не
проверка приватных материалов/БД/юридического качества. Перед выпуском нужны
реальный disposable PG/MinIO прогон, весь pytest, Ruff/mypy/Compose/security,
container imports, визуальный PDF и независимая проверка renderer.
Живой Hermes прогон и Telegram API доставка явно отделяются от mocked PTB
callbacks; настаивать на доказательстве полного ручного Telegram click-through
без пользовательской сессии нельзя. Никаких автоматических legal approvals
или передачи 67 частных DRAFT эталонов модели.

## Результат объединённой проверки

2026-10-13: 2028 passed / 0 skipped за 91.05 секунды с включёнными
POSTGRES_INTEGRATION и MINIO_INTEGRATION на отдельных tmpfs-контейнерах;
все входные материалы синтетические. 196 предупреждений PTB о deprecated
retry_after и ограничениях тестового ConversationHandler/JobQueue не скрыты.
Ruff, mypy всех трёх сервисов (136 файлов), strict pip-audit, базовый и
production Compose, сборка реального gateway Dockerfile и import его entrypoint
non-root/offline/read-only прошли. Точные worktree Code Graph: 528 nodes / 527
edges, warnings=0; MCP index original root отдельно refreshed.

Renderer review e1ff194 нашёл P2: компактная Telegram-карточка обрезала
юридический вывод до его оговорки/ссылок. Исправлено нейтральной отсылкой
к полному PDF вместо частичного вывода или рекомендации. Визуальный просмотр
выявил первый лист длинного PDF без фабулы; pagination исправлена и закреплена
тестом. Fix-only независимая перепроверка: новых actionable defects нет.
PDF READY/HIGH/BLOCKED и четырёхстраничный long проверены; сохранённые bytes
исторических отчётов и их hashes не меняются.

Это техническая проверка поддерживаемых сценариев с реальными Core request
validators и отдельными настоящими PG/MinIO-интеграциями. Telegram I/O и модели
в регрессионных тестах заменены; полный ручной Telegram click-through и новый
живой Hermes-анализ обеих веток этим прогоном не доказаны. Read-only production
baseline: main0b0a882, все восемь сервисов healthy. Новые изменения не развёрнуты:
необходимы GitHub CI, согласованный release и post-deploy synthetic checks.

### CI correction, 2026-10-14

Run 38461782981 exposed an environment mismatch: quality and PostgreSQL suites
failed because the clean runner lacked pdftotext (12 PDF failures); security,
container imports, Ruff/mypy and dependency audit passed. Neither tests nor the
gate are disabled. Both host test jobs now explicitly install poppler-utils and
fonts-dejavu-core, verify tool availability before testing, and have two RED/GREEN
workflow regression tests. After restoring the stopped disposable tmpfs test
services, the unchanged application plus CI checks passed 2030 tests with no
skips (76.44 seconds). Remote CI and the extended scenario audit remain pending;
this does not claim that PR94 has deployed or that every user behaviour is proven.
