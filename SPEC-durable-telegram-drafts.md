# Specification: durable Telegram intake drafts

## Status

Approved for incremental implementation on 2026-08-31. The product flow and 30-day automatic
purge for unsubmitted drafts were confirmed by the product owner. This is a `case-core` and
`telegram-gateway` extension; it does not enable recommendations, external LLM processing or
patient communication.

## Objective

A clinic administrator must be able to start several pseudonymous incident cards, leave a card
without losing progress, open the list of their unfinished cards, switch to one and continue from
the exact next question after a bot restart or Telegram session change.

Creating a draft must not create a legal case, report or recommendation. A `cases` record remains
created only after the administrator reviews and confirms a complete intake.

## Product behaviour

- **New case** creates one server-side draft and opens its first question.
- **My drafts** lists the active drafts that belong to the current administrator and clinic,
  newest first. The list shows only a safe generated label: incident category, current step and
  last-update time; it never displays the free-text summary or patient reference.
- Selecting a draft makes it the active conversation and repeats the exact outstanding question.
  Switching drafts saves the current answer before opening the next draft.
- Opening `/menu`, using its back button or a conversation timeout saves the current draft and
  exits the in-memory conversation. It does not discard the draft.
- An explicitly confirmed discard action archives a draft. Submitted drafts become unavailable in
  the active list after a successful idempotent report submission.
- The first release permits up to 20 active drafts per administrator and clinic. It has no public
  sharing, assignment, patient identity, document upload or cross-clinic selection.
- `DRAFT` and `ARCHIVED` records are eligible for automatic irreversible purge 30 days after
  their last update. `SUBMITTED` records are not an independent retention store: the existing
  confirmed case/report policy applies instead.

## Persistence and authorisation

- Add a tenant-owned `telegram_intake_drafts` table through an Alembic migration. It contains
  `clinic_id`, `actor_membership_id`, a random UUID, `status`, next `wizard_state`, validated
  JSON draft data, optimistic `revision`, and UTC creation/update timestamps.
- PostgreSQL RLS binds every row to the server-established clinic context. API queries additionally
  bind the row to the resolved membership; another administrator, including one in the same
  clinic, cannot read, update, list or archive it.
- The bot may cache the selected draft in Telegram `user_data`, but Legal Core is the source of
  truth. Each accepted wizard transition persists its complete current snapshot before the next
  question is offered.
- JSON draft data is bounded in size and shape. It may hold the already permitted pseudonymous
  intake facts, but it is not placed in logs, audit metadata, tests or the legal research library.
- Updates use the returned revision and fail safely on a concurrent modification; the bot reloads
  rather than silently overwriting another current session.

## Legal Core contract

All endpoints resolve actor and clinic from `X-Telegram-User-Id`; neither is accepted in payload.
All state changes require `Idempotency-Key`.

```text
POST /v1/telegram-intake-drafts
GET  /v1/telegram-intake-drafts
GET  /v1/telegram-intake-drafts/{draft_id}
PUT  /v1/telegram-intake-drafts/{draft_id}
POST /v1/telegram-intake-drafts/{draft_id}/archive
```

List responses contain safe summaries only. The detail and update contracts carry the bounded
draft snapshot and next `wizardState`. `PUT` includes the expected revision and returns the new
revision. An update with a stale revision returns a stable conflict code and no data from another
draft. The existing report-submission endpoint remains idempotent; the gateway archives the draft
only after it receives the successful/replayed report response.

## Telegram boundary

- Add main-menu entry **Мои черновики** and a compact inline draft list.
- The existing conversation handler accepts a draft-resume callback as an `allow_reentry` entry
  point. It restores only a server-authorised draft and returns the persisted next wizard state.
- A small transition wrapper saves a draft after every successful state change, avoiding a
  divergent in-memory-only path. It is not applied to completed report recovery or administrative
  commands.
- A restart loses only the local selection, never the saved draft. The administrator reopens it
  from **Мои черновики**.

## Commands

```bash
.venv/bin/python -m pytest
.venv/bin/python -m ruff check .
.venv/bin/python -m mypy services/legal_core/src services/gateway/telegram/src
docker compose config --quiet
```

For PostgreSQL API and migration checks, create a uniquely named disposable `POSTGRES_DB`, run
`alembic upgrade head`, run the selected test file with `POSTGRES_INTEGRATION=1`, then drop only
that exact disposable database.

## Success criteria

- Two active drafts of one administrator survive a gateway restart and can be resumed in either
  order from their saved next question.
- Leaving to the menu and switching drafts do not create `cases`, facts, reports or duplicate
  drafts.
- A different user or tenant receives no draft details and cannot mutate a draft by UUID.
- An archived/submitted draft is excluded from active drafts and cannot be resumed.
- Stale revision and idempotent replay behaviour are deterministic and tested.
- Existing tenant, subscription, evidence and no-auto-send gates remain unchanged.

## Boundaries

- Always: validate Telegram/API input, enforce server-side tenant and membership scope, use an
  Alembic migration, record only PII-free audit metadata, and add contract/authz tests.
- Ask first: adding a new personal-data category, sharing a draft with another user, changing
  retention/deletion policy, or connecting an external service.
- Never: use a draft as legal evidence, store raw Telegram updates in logs, create a case before
  confirmation, or silently overwrite a stale draft.
