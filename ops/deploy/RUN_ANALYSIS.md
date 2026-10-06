# Запуск анализа с Hermes на production

`DEPLOY_ANALYSIS_ENABLED=0` в `/etc/dental-legal-ai/app.env` запускает режим приёма
обращений: карточки, уточнения, документы и рабочие места доступны, но вызовы LLM
отключены. Отсутствующий флаг также означает `0`.

`DEPLOY_ANALYSIS_ENABLED=1` подключает два закреплённых Hermes-сервиса и
agent-orchestrator. Telegram и фоновый анализ Legal Core получают внутренний адрес
оркестратора автоматически. Флаг хранится на VPS, поэтому последующие deployment
и rollback используют выбранный режим; обычный deployment не отключает анализ.

## 1. Подготовить существующий deployment

Используйте проверенный checkout с этой инструкцией. На существующем VPS от root
обновите установленный deploy-скрипт; он намеренно не обновляется автоматически:

```bash
cd /srv/dental-legal-ai/repository
install -m 0750 ops/deploy/deploy-commit.sh /usr/local/sbin/dental-legal-ai-deploy
python3 --version
docker compose version
```

Для нового VPS используйте `bootstrap-server.sh` из [основной инструкции](README.md).
Нужны Docker Compose с поддержкой `!override`/`!reset` (не ниже 2.24.4), Python 3,
прямой доступ к Telegram Bot API и заполненные базовые параметры `app.env`.

## 2. Подготовить модель и правовую базу

Используйте уже выбранного и проверенного OpenAI-compatible провайдера. Эта
инструкция не выбирает провайдера и не включает платные услуги. Подготовьте его
URL, API key и точные идентификаторы двух моделей; допустима одна модель для обеих
ролей, но Hermes-контейнеры и их внутренние ключи должны оставаться разными.

До реальных консультаций остаются обязательными существующие проверки:
утверждение корпуса платформенным LEGAL_EDITOR, risk-policy, условия обработки
данных у провайдера и назначение ответственного юриста. Запуск контейнеров сам
по себе ничего не утверждает. Неодобренный корпус и отсутствующая policy продолжают
блокировать юридические рекомендации. См. [условия пилота](../../docs/closed-pilot-readiness.md).
Учтите также [порядок обновления MinIO](../../docs/audit/minio-image-2026-09-15.md):
перед первым deployment или заменой закреплённой версии соберите security release из
исходников в отдельное окно обслуживания и проверьте существующий volume на резервной
копии. Обычный deployment повторно его не собирает.

## 3. Собрать закреплённый Hermes

На VPS, где будет работать Docker:

```bash
cd /srv/dental-legal-ai/repository
sh ops/hermes/build-pinned-image.sh
```

Скрипт собирает только commit из ADR-0013. Deployment потребует этот локальный
образ; он не скачивает одноимённый образ из случайного registry. Две модели
обслуживаются внешним провайдером, но два Hermes-процесса требуют дополнительной
RAM: проверьте её расход на стенде, особенно на исходном VPS с 2 GiB памяти.

## 4. Заполнить конфигурацию и запустить

Отредактируйте root-owned `/etc/dental-legal-ai/app.env` с правами `0600`, используя
список из `production.env.example`. Не вставляйте значения ключей в команды, Git,
issue или журнал CI.

| Переменная | Значение |
| --- | --- |
| `DEPLOY_ANALYSIS_ENABLED` | Буквально `1`, без кавычек и подстановок |
| `HERMES_LLM_BASE_URL` | URL существующего проверенного OpenAI-compatible провайдера |
| `HERMES_LLM_API_KEY` | Его API key |
| `HERMES_RESEARCHER_LLM_MODEL` | Точное имя модели первого прохода |
| `HERMES_REVIEWER_LLM_MODEL` | Точное имя модели проверки |
| `AGENT_INTERNAL_KEY` | Отдельный случайный ключ, минимум 32 символа |
| `HERMES_RESEARCHER_API_KEY` | Другой случайный внутренний ключ, минимум 32 символа |
| `HERMES_REVIEWER_API_KEY` | Третий случайный внутренний ключ, минимум 32 символа |

`AGENT_ORCHESTRATOR_URL` вручную не задавайте: его устанавливает overlay. После
заполнения запустите обычный проверенный GitHub deployment или выполните от root:

```bash
cd /srv/dental-legal-ai/repository
reviewed_revision=$(git rev-parse HEAD)
/usr/local/sbin/dental-legal-ai-deploy deploy "$reviewed_revision"
```

Commit должен быть в `origin/main`. До изменения контейнеров скрипт проверяет
Compose, параметры анализа, независимость внутренних ключей и локальный Hermes
образ. Ошибки не печатают resolved configuration или ключи. Затем deployment
ожидает готовность всего выбранного стека до 180 секунд.

## 5. Проверить результат

```bash
docker compose --project-name dental-legal-ai --env-file /etc/dental-legal-ai/app.env \
  -f docker-compose.yml -f ops/deploy/docker-compose.production.yml \
  -f ops/hermes/docker-compose.hermes.yml --profile analysis ps
```

Проверьте здоровое состояние Legal Core, Telegram, orchestrator и обоих Hermes.
На синтетическом обращении пройдите уточнения → подтверждение → анализ → результат.
Успешный healthcheck не доказывает доступность модели, правильность API key провайдера
или качество ответа; это подтверждает только сквозной синтетический сценарий.

Если потребуется остановить LLM-вызовы, задайте `DEPLOY_ANALYSIS_ENABLED=0` и
повторите тот же deployment. Он очистит адрес оркестратора у Telegram и Legal Core
и уберёт отключённые контейнеры. Сохранённые обращения и named volumes сохраняются.
Перед rollback к commit, где ещё нет этих overlays/preflight, явно выберите базовый
режим; изменение схемы БД по-прежнему требует отдельного плана восстановления.
