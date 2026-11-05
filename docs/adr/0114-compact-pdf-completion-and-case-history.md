# ADR 0114: compact PDF completion and personal case history

Date: 2026-11-05. Status: accepted by the owner's request for attached reports and clear history.

## User behavior

A verified READY report replaces the durable Telegram waiting message with its PDF,
using editMessageMedia (Telegram supports replacing text messages with media). Its
caption contains only the public case number and completion status, not the complaint,
recommendations, citations or service metadata. There is no new «Результат» button.
Old result callbacks remain usable and queue the same authorized PDF instead of
reprinting its contents. The saved canonical report/PDF itself is unchanged.

The completion keyboard opens the case or «Мои кейсы». Main-menu users with existing
case-creation access also have «Мои кейсы» and /cases. History shows bounded pages of
their own unexpired cases, with status and date. Each case offers its saved report
versions; intake cards without recommendations are labelled separately. Assigned
lawyer work remains in the existing escalation workspace. NEEDS_FACTS, LIMITED and
HUMAN_REVIEW receipts do not become final legal reports or acquire fabricated PDFs.

## Delivery and recovery

The existing job identity and durable message binding remain authoritative. Replays
edit that same message; they do not append another file after each gateway restart.
The notifier acknowledges completion only after successful media editing (or the
existing recognized no-op/deleted-placeholder conditions). Transfer failure leaves
the notification pending for retry. Only one automatic PDF transfer is held in memory
at a time, with a 20-second wall inside the existing 25-second notification batch.
Manual downloads retain the bounded background file queue and do not change whatever
input the user has opened meanwhile. Navigating from a document opens a separate
menu, preserving the report's caption and keyboard context.

## Read-only interface

GET /v1/case-history?offset=N returns at most eight CaseResponse metadata items and
nextOffset. GET /v1/case-history/{caseId}?offset=N returns the owned case, at most
eight report metadata items (id, version, creation date, READY/CARD), nextOffset and
an optional existing escalation ID. No case facts, complaint text, report JSON or PDF
blobs are included. All requests resolve clinic/creator/entitlement on the server;
offset is bounded. History is personal for OWNER, ADMIN and LAWYER alike. A colleague's
case is not exposed by these new routes, even within the same clinic. Existing
escalation permissions are unchanged.

PDF callbacks contain a canonical compact pair of immutable case/report IDs and a
page offset within Telegram's 64-byte limit. The selected report is checked against
that case page inside the file queue, then the existing PDF endpoint independently
reauthorizes access/retention. Opaque IDs never act as permission. No table, migration,
new personal-data category, retention extension, risk change, model change or source
approval is introduced. The existing 30-day content-retention policy remains.

## Verification

Tests cover short captions, no repeated report prose/result button, actual PTB
multipart encoding, same-message upload and ACK ordering, transfer failure, historical
downloads through real PTB dispatch, queued revocation, old callback compatibility,
pagination, own/foreign/same-clinic creator isolation, expiry, and PDF retrieval.
The installed version must also attach an existing owner-only synthetic report through
the real Telegram API and read history under owner and lawyer identities before the
feature is declared delivered.

## Production packaging follow-up

The first rollout exposed a missing selectively copied shared history DTO in the
Telegram image. Production was rolled back to the last healthy revision. The image
now copies the DTO and constructs both application configurations during the build.
CI additionally executes this construction as the image's non-root user with no
network and a read-only filesystem, so lazy handler imports are tested before release.
