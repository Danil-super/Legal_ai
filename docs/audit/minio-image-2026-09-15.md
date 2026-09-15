# MinIO: исправленный выпуск из закреплённых исходников, 15.09.2026

CI останавливался на загрузке `minio/minio:RELEASE.2025-04-22T22-12-26Z`
из Docker Hub с `pull access denied`. Проверка официального Quay подтвердила наличие
того же выпуска, но также выявлена применимая к нему CVE-2025-62506. Поэтому
**устаревший Quay-образ не используется по умолчанию**: Compose и CI собирают
исправленный `RELEASE.2025-10-15T17-29-55Z` из официальных исходников.

## Текущая сборка

- Upstream: `https://github.com/minio/minio.git`.
- Release: `RELEASE.2025-10-15T17-29-55Z`.
- Commit: `9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a`, подтверждён
  [GitHub API тега](https://api.github.com/repos/minio/minio/git/ref/tags/RELEASE.2025-10-15T17-29-55Z).
- [go.mod этого commit](https://github.com/minio/minio/blob/9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a/go.mod)
  указывает Go `1.24.0` и toolchain `go1.24.8`; builder использует
  `golang:1.24.8-bookworm`, `GOTOOLCHAIN=local` и `GOFLAGS=-mod=readonly`.
- `ops/minio/Dockerfile` проверяет SHA checkout и toolchain, запускает
  `go mod download`/`go mod verify`, применяет флаги `-tags kqueue -trimpath`
  из [upstream Makefile](https://github.com/minio/minio/blob/9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a/Makefile)
  и официальный `gen-ldflags.go` с фиксированной датой выпуска.
- Runtime основан на Debian bookworm-slim, содержит CA certificates, curl,
  собранный MinIO, upstream entrypoint и LICENSE. Старый upstream Dockerfile
  не используется, поскольку он сам зависит от недоступного `minio/minio:latest`.
- Локальное имя: `dental-legal-minio:9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a`.
  Compose собирает его; CI сначала собирает тот же Dockerfile, затем выполняет
  существующие S3/MinIO integration tests. Никакие сторонние копии MinIO не используются.

Исходники и Go-зависимости закреплены. Это не обещание побитовой идентичности
образов: базовые container tags и Debian packages получают обновления. Локально
Docker отсутствовал; фактическую сборку и S3-проверки подтверждает CI.

## Диагностика прежнего Quay-образа

- [Официальный README данного выпуска](https://github.com/minio/minio/blob/RELEASE.2025-04-22T22-12-26Z/README.md)
  указывает `quay.io/minio/minio` как источник контейнера.
- [Quay API: конкретный тег](https://quay.io/api/v1/repository/minio/minio/tag/?specificTag=RELEASE.2025-04-22T22-12-26Z)
  ответил 15.09.2026: `manifest_digest` =
  `sha256:a1ea29fa28355559ef137d71fc570e508a214ec84ff8083e39bc5428980b015e`.
- [Registry V2: закреплённый manifest](https://quay.io/v2/minio/minio/manifests/sha256:a1ea29fa28355559ef137d71fc570e508a214ec84ff8083e39bc5428980b015e)
  доступен без аутентификации; содержит Linux `amd64`, `arm64`, `ppc64le`.

Эти сведения сохраняются для диагностики прежней ошибки загрузки, а не как
рекомендация возвращаться к уязвимому апрельскому выпуску.

## Исправленная уязвимость и обновление работающего сервера

Апрельский выпуск затронут **CVE-2025-62506 / GHSA-jjjj-jwhf-8rgr**, High, CVSS 8.1.
Учётная запись service account или STS с ограниченной session policy может создать
дочернюю учётную запись с более широкими правами родителя. Исправление находится в
`RELEASE.2025-10-15T17-29-55Z`; производитель не указывает workaround.
Источник: [официальный advisory MinIO](https://github.com/minio/minio/security/advisories/GHSA-jjjj-jwhf-8rgr).

Новая сборка включает указанное исправление. Это не заменяет отдельный аудит
всех зависимостей и конфигурации. MinIO по-прежнему не публикует host-порт и
доступен только через внутреннюю сеть приложения.

Для существующего сервера сначала сделайте резервную копию MinIO volume и проверьте
восстановление. Выполните сборку и проверку S3 API, существующих объектов и политик
на копии стенда, затем обновите production. Путь данных `/data` сохранён, но
совместимость конкретного существующего volume должна быть подтверждена стендом.
Не выполняйте автоматический downgrade уже обновлённого хранилища: восстанавливайте
проверенную резервную копию. [Официальные release notes](https://github.com/minio/minio/releases/tag/RELEASE.2025-10-15T17-29-55Z)
подтверждают переход этого security release на сборку из исходников.

Сборка нового MinIO включена в Compose по умолчанию и может занять заметное время
при первом deployment. Go parallelism ограничен двумя задачами для умеренного
расхода памяти; проверка доступных RAM/диска всё равно необходима на целевом VPS.
Работающий сервер в рамках этой правки не обновлялся.
