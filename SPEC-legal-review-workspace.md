# Specification: platform legal-editor review workspace

## Status

Approved for incremental implementation on 2026-09-09. This specification implements the
existing human-only approval boundary; it does not approve any legal version, alter the trusted
source allowlist, activate recommendations, risk policy, Hermes, or a patient-facing response.

## Objective

An active platform `LEGAL_EDITOR` must be able to review an immutable official legal artifact in
Telegram before making a conscious, checksum-bound approval decision. The reviewer needs the
exact preserved artifact, official source link, effective dates, checksums and bounded extracted
fragments—not merely a status label. Legal Core remains the only component permitted to record
the decision and make an approved version eligible for retrieval.

`CLINIC_LAWYER` handles the `HIGH`/`CRITICAL` case-escalation queue. It is not a source-approval
role. `LEGAL_EDITOR` is global platform authority and does not gain access to clinic cases,
subscriptions, tenant documents, risk-policy writes, or any Hermes capability.

## Product behaviour

- An active `LEGAL_EDITOR` sees **⚖️ Проверка норм** in the main menu. The button is absent for
  every other user; a forged callback receives the same generic access denial as a direct API
  request.
- The workspace lists at most 20 immutable versions per page, newest first. Each row identifies
  title, official number, approval state and whether it is technically approvable.
- A version detail card contains only public legal metadata: document title/issuer/number,
  official URL, artifact MIME type/size/page count, effective dates, artifact retrieval time,
  SHA-256 values, fragment count and approval state.
- The reviewer can open the official URL and request the exact stored public artifact from Legal
  Core. The gateway accepts only a bounded response (at most 50 MB), relays it as a Telegram
  document and never logs its bytes. This is a preserved public legal publication, not a patient
  file or a mechanism for Telegram uploads.
- The reviewer can page through bounded selected fragments. The gateway renders a fixed maximum
  number and length of fragments per page, with no free-text search and no model involvement.
- A version is approvable only when it is `REVIEW_REQUIRED`, `OFFICIAL_RAW`, complete and passes
  the existing server regression checks. A legacy `NORMALIZED_EXCERPT` is visibly
  **неодобряемая старая версия**; it has no approval action.
- Approval is a two-step action. The editor explicitly attests that the source is official, the
  artifact is complete, effective dates are verified and selected fragments are verified, then
  confirms **Одобрить версию**. All four attestations are required.
- Legal Core binds the action to the version ID, all three server-presented SHA-256 values and
  effective-date range. A changed version produces a stable conflict and no approval event; the
  editor must reopen it. A successful action reuses the existing immutable approval service and
  its audit event. It is never automatic or delegated to a model.
- This first slice has no **Block** button. The database lifecycle does not yet persist a
  `REVIEW_REQUIRED → BLOCKED` transition, so presenting that control would falsely imply that it
  works. A separate versioned lifecycle change is required before adding it.

## Legal Core contract

All editor endpoints require two independent server-side checks: an active
`users.system_role = LEGAL_EDITOR` for `X-Telegram-User-Id`, and a constant-time match of the
private `X-Legal-Editor-Gateway-Key` header. The key is injected only by the Telegram gateway from
`LEGAL_EDITOR_GATEWAY_KEY`; it is at least 32 characters, is never written to logs/callbacks, and
is not reused for Hermes or any other service. If the key is absent or invalid, the editor surface
is disabled and Legal Core fails closed. A client-supplied clinic, subscription, role or configured
platform-owner ID is never a substitute. The corpus is platform-global and no editor endpoint
returns tenant-owned records.

```text
GET  /v1/legal/editor/status
GET  /v1/legal/review-queue?page={page}
GET  /v1/legal/review-queue/{version_id}
GET  /v1/legal/review-queue/{version_id}/artifact
GET  /v1/legal/review-queue/{version_id}/fragments?page={page}
POST /v1/legal/review-queue/{version_id}/approval-events
```

The candidate list has a fixed page size of 10 and page range `1..100`, ordered by
`received_at DESC, id DESC`; the fragment list has page size 5 and the same page range, ordered by
`ordinal ASC, id ASC`. An out-of-range page returns an empty items list and stable pagination
metadata. The list/detail/fragment responses use typed additive JSON contracts. The artifact
endpoint returns stored bytes only after the same editor check; it never fetches a URL, follows
redirects or accepts a path. It verifies actual byte length is at most 50 MB, permits only
`application/pdf` and `text/plain`, and sets a UUID-derived attachment filename.

The approval request includes the four boolean attestations, expected raw/normalized/fragments
hashes and expected effective dates. It requires a UUID `Idempotency-Key`, preserves the
established error envelope, rejects an in-flight/stale confirmation safely and returns the new
approved-version summary. The request hash and idempotency key are recorded on the immutable
`LegalApprovalEvent` in an Alembic migration: the same editor/key/payload replays the original
approved version, while reuse with another payload conflicts. A PostgreSQL advisory lock serializes
the editor/key pair. Under the same transaction and version lock, Legal Core preflights
`REVIEW_REQUIRED`, `OFFICIAL_RAW` and every technical check before inserting any event. A stale,
legacy, already-approved or technically invalid request produces no false `BLOCKED` decision.

## Telegram boundary

- Callback data contains only a fixed action, UUID and bounded page number; every callback is
  matched strictly and is re-authorized by Legal Core.
- Pending attestation state belongs to one Telegram user and one version. It contains only
  booleans and server-displayed version identity, not legal text, case content or credentials.
- Every screen has **← Главное меню**. Errors are generic and do not reveal whether an arbitrary
  version ID exists.
- The main menu keeps existing owner, administrator and clinic-lawyer paths unchanged. The
  escalation label is corrected to **⚖️ Эскалации HIGH/CRITICAL**; both levels retain their
  existing human-review policy.

## Tech stack and commands

Python 3, FastAPI/Pydantic/SQLAlchemy in `services/legal_core`, and python-telegram-bot in
`services/gateway/telegram`. No dependency, LLM, storage system or database table is added; an
Alembic migration extends the existing immutable approval-event table for replay safety.

```bash
.venv/bin/python -m pytest
.venv/bin/python -m ruff check .
.venv/bin/python -m mypy services/legal_core/src services/gateway/telegram/src
docker compose config --quiet
```

## Testing strategy

- Contract/API tests prove active editor access, non-editor and inactive-user rejection, no
  clinic/subscription surrogate, legacy rejection, bounded artifact response, stale hash conflict,
  idempotent replay and checksum-bound audit/retrieval transition.
- Telegram tests prove menu visibility, forged callback denial, detail/fragment pagination,
  attestation reset on version switch, confirmation and generic failure paths.
- PostgreSQL integration tests prove the existing database guards still reject any approval without
  an append-only human approval event.
- The full quality gate, dependency audit and production health check run before deployment.

## Boundaries

- Always: validate external input; server-authorize each endpoint; keep artifacts and callbacks
  bounded; preserve audit without raw document/case text; add authz and regression tests.
- Ask first: trusted-source expansion, approval of a legal version, a risk-policy change, new data
  category, external LLM/provider, or any patient-message capability.
- Never: auto-approve, approve through Hermes/LLM, expose tenant data in the global workspace,
  accept artifact URLs/uploads from Telegram, or use draft/review content as a legal conclusion.

## Success criteria

1. A qualified editor can inspect an exact official candidate and explicitly approve it; the
   approved-only library then displays the applicable version.
2. A clinic lawyer, owner without `LEGAL_EDITOR`, inactive user and forged callback cannot view
   candidates, artifacts, fragments or perform approval.
3. A stale or legacy candidate cannot create an approval event or become retrievable.
4. No recommendation, source discovery, risk assessment, Hermes request or patient message is
   enabled by this workspace.
