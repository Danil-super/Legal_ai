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

The Legal Core startup applies migrations and idempotently ingests checksum-locked manifests for
Government Decrees No. 736 and No. 659 as `REVIEW_REQUIRED`. They currently contain normalized
review excerpts, not official raw PDF artifacts, and therefore cannot be approved. The
applicability boundary is `[2023-09-01, 2026-09-01)` for No. 736 and
`[2026-09-01, 2031-09-01)` for No. 659. Ingestion never approves law for production retrieval.

Approval is a separate CLI operation for an active `LEGAL_EDITOR`. It requires an exact official
artifact checksum, expected effective dates and four explicit human attestations. The database
stores successful and blocked attempts in an append-only audit table; production retrieval also
requires `artifact_kind=OFFICIAL_RAW`.

When an official PDF has been downloaded manually, prepare its immutable snapshot before
ingestion. The command checks the `%PDF-` signature, 50 MB limit, `pdfinfo` metadata, published
page count and published byte length, independently recorded SHA-256, act number/date in the
extracted full text and exact fragment substrings. Image-only publications use a clearly labelled
Russian Tesseract OCR fallback; OCR is never treated as the source of truth and must be reviewed
against the immutable PDF. The command only emits a `REVIEW_REQUIRED` manifest:

```bash
mkdir -p services/legal_core/corpus/official
docker compose run --rm --no-deps \
  -v /absolute/path/to/downloads:/input:ro \
  -v "$PWD/services/legal_core/corpus/official:/output" \
  legal-core python -m legal_core.official_artifact \
  --pdf /input/pp736.pdf \
  --base-manifest /app/services/legal_core/corpus/initial_pp736.json \
  --output-directory /output \
  --retrieved-at 2026-08-22T12:00:00+00:00
```

Review the generated normalized text and fragments, then ingest the generated JSON with
`python -m legal_core.corpus_loader`. Ingestion still cannot make it visible to production
retrieval; the independent `LEGAL_EDITOR` approval gate remains mandatory.

To connect the first clinic administrator securely:

1. Open the bot and send `/whoami`.
2. Put the returned numeric ID into `BOOTSTRAP_TELEGRAM_ADMIN_ID` in `.env` and set
   `BOOTSTRAP_CLINIC_NAME`.
3. Run `docker compose up -d --force-recreate legal-core`.
4. Run the bootstrap once more to obtain the printed `membership_id` (the operation is idempotent):

```bash
docker compose exec legal-core python -m legal_core.bootstrap_admin
```

5. After the purchase has been verified by the service operator, grant access to that exact
administrator and clinic. This command stores no payment data and never changes the user's role:

```bash
docker compose exec legal-core python -m legal_core.subscription_provisioning \
  --membership-id <membership_id> \
  --plan-code MVP_MONTHLY \
  --starts-at 2026-08-22T12:00:00+00:00
```

6. Open `/menu` and choose `📝 Создать кейс`.

The bootstrap is idempotent and does not register unknown Telegram users automatically. A mapped
administrator without an active, current subscription cannot open cases or use legal retrieval;
the bot gives a neutral support message. Use the same internal command with `--status SUSPENDED`
or `--status CANCELLED` to stop access. Payment checkout, cards and provider webhooks are not
implemented in this MVP.

Apply the bot name, descriptions, commands and avatar as an explicit one-off operation:

```bash
docker compose run --rm telegram-gateway python -m telegram_gateway.profile
```

Telegram strictly rate-limits profile mutations, so they are deliberately not repeated on
every polling restart.

`docker compose down` keeps the named data volumes. Add `--volumes` only when intentionally deleting local PostgreSQL, Redis and MinIO data.

The Telegram gateway uses long polling and exposes no host port. `/start` and `/menu` open a
branded, image-based inline menu. A mapped `CLINIC_ADMIN` with an active subscription can fill a
pseudonymous case card, confirm it and receive the canonical PDF. Cancelling before confirmation
creates no database case. The PDF remains an intake record: legal recommendations and a
patient-response draft are explicitly blocked until an approved evidence corpus and verifier gate
are available.
