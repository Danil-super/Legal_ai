# Specification: guided case intake and anonymised materials, v2

## Status and authority

The owner requested and approved this product flow on 2026-09-30. It replaces the
new-case experience for newly created drafts only. Existing v1 drafts remain
resumable and are not silently reinterpreted. This authorisation covers optional,
**anonymised** materials supplied by an authorised clinic user; it does not authorise
real patient-data processing, a new external provider, automatic legal approval,
or automatic delivery of a legal response to a patient.

The normal 30-day unsubmitted-draft and 90-day confirmed-case retention periods
already approved for case content apply to the material metadata and raw object.
They are not extended by this specification.

## Objective

An administrator should be able to describe a problem in ordinary language rather
than determine its legal qualification, deadline, harm or legal remedy. Legal Core
must persist only confirmed facts, identify missing information explicitly, and
fail closed to a human workflow when applicable approved evidence is insufficient.

## User journey

Each screen contains **Назад** and **В главное меню**. Back changes only the
current draft and clears dependent answers when a parent answer changes. The bot
never asks the administrator to decide whether the situation is legally a
``претензия`` or whether a statutory deadline applies.

1. **What arrived from the patient.** Choose a plain description: message/request,
   dissatisfaction/complaint, formal document, court/authority document, another
   channel, or `Не уверен`. An optional anonymised text or file may be attached.
2. **What the situation concerns.** Choose one or more: treatment, service or
   interaction, medical records, personal data/images, or other. For treatment,
   name one or more affected services in short free text; `Не знаю` is valid.
3. **What happened and when.** Give a short factual description and an exact date
   where known. State whether this is the first occurrence or an ongoing conflict.
   An unknown/approximate date is explicitly marked; it cannot be silently treated
   as an exact legal date.
4. **What the clinic has already done.** Select any applicable action (invited for
   examination, continues treatment, held a commission, proposed correction/refund,
   removed material, ended contract, made an agreement, other) and optionally add
   a concise anonymised note.
5. **Health consequences known to the clinic.** State whether there is information
   about a complication/worsening, another clinic, hospitalisation, or another
   consequence. `Нет сведений` is not a statement that there was no harm.
6. **Optional clinic material.** Add only material relevant to the case in
   anonymised form. The screen makes clear that identifiers, contact details,
   images capable of identifying a person, and unnecessary medical data must be
   removed before upload. The user may continue with no attachment.
7. **Factual summary and confirmation.** Legal Core renders a deterministic,
   labelled summary of supplied facts and unknowns. The administrator can correct
   a section or explicitly confirm the summary. It is not a legal assessment,
   a deadline calculation, an admission of liability or a message to the patient.

## Fact and analysis contract

New facts distinguish the ordinary-language intake from legal classification:

- incoming communication kind and whether an anonymised source was supplied;
- situation areas and affected service labels;
- event narrative, known event date and `FIRST`/`ONGOING` chronology;
- clinic actions already taken;
- known health-consequence signals, including `UNKNOWN` separately from `NO`;
- optional material presence and a metadata-only material reference.

V2 does not write inferred values into legacy claim/deadline fields. A compatibility
adapter represents absent legacy facts as `UNKNOWN`, never `NO` or a guessed date.
Case risk and retrieval use only confirmed facts and date-applicable `APPROVED`
normative versions. Clinical references and material uploads do not themselves
become legal evidence.

Before queuing analysis, Legal Core runs a deterministic sufficiency assessment:

- If a small, relevant fact is missing, return exact follow-up prompts to the same
  draft/case; do not tell the user to create another case.
- If the date is unknown where date applicability is necessary, request it or state
  that the system cannot calculate a legal time position.
- If the event/health signals are HIGH or CRITICAL, create the existing authorised
  escalation record for an eligible clinic lawyer/owner.
- If no date-applicable approved legal evidence covers the supported topic, abstain
  with `LEGAL_EVIDENCE_UNAVAILABLE` and offer the same authorised escalation path.
- A LOW/MEDIUM output remains an internal, evidence-bound draft for the clinic
  user. It is never automatically sent to a patient.

The model layer receives only the existing minimised analysis context after all
evidence, tenant and risk gates. Raw uploaded bytes, filenames, object keys and
unredacted material content are excluded from model prompts and logs.

## Material boundary

The new material path is separate from the long-lived clinic-document library and
from the legal corpus. It accepts only bounded PDF, TXT and DOCX payloads after
server-side size, MIME/signature and parser-limit validation. Unsupported, encrypted,
corrupted or oversized uploads are rejected without storing their bytes. Original
filenames are normalised to a safe display label; a SHA-256 is used for integrity
and idempotency but not placed in public Telegram text.

Each material row has `clinic_id`, the exact uploader membership, a draft-or-case
owner (exactly one), opaque object identifier, safe type/size/checksum metadata,
timestamps and an expiry inherited from its owner. PostgreSQL RLS and explicit
membership checks apply to every list, upload, download and delete. Legal editors,
Hermes and other clinics do not obtain case-material access by role alone.

Raw bytes are stored once in private object storage. The transition from a confirmed
draft to a case preserves the same private object and changes only authorised
ownership metadata; it does not copy content into reports, audit events or logs.
Retention uses a durable deletion queue: an object is removed before its metadata
is irreversibly purged, and a failed storage deletion is retried without making the
content visible after expiry. Tests use generated non-personal bytes only.

## API and Telegram boundaries

Legal Core owns the state machine, material ownership, retention and access checks.
The gateway never accepts a clinic identifier from Telegram and uses short,
opaque callback values. All writes are idempotent and mutation responses include a
fresh revision. New REST routes receive an explicit OpenAPI/Pydantic contract and
negative authz/tenant tests before registration. Download is an authenticated
streaming response; no presigned public URL is issued.

The gateway acknowledges a file callback before bounded upload/parsing work. The
upload path has a per-user and per-clinic Redis limit and a durable retry/status
record; unrelated users' menu actions cannot wait behind it. In-memory Telegram
state remains a cache only, and v1/v2 draft versions dispatch through separate
handlers until v1 draft retention expires.

## Delivery slices and acceptance

1. Publish typed v2 facts, deterministic summary/sufficiency logic, and synthetic
   red/green tests; keep current v1 flow intact.
2. Add material metadata, deletion queue, RLS/grants, Alembic migration, private
   storage deletion capability and PostgreSQL tenant/retention/authz tests.
3. Add protected Core upload/list/download/delete routes with parser, MIME, size,
   idempotency and no-raw-log tests.
4. Add the v2 Telegram draft flow, visible navigation and bounded upload UI; test
   separate users, restart/resume, stale buttons, back/correction and unrelated
   menu responsiveness.
5. Connect deterministic follow-up/escalation decisions and create lawyer-reviewed
   synthetic gold cases for each released legal-coverage group. Until this gate is
   complete, the system abstains rather than claiming complete legal coverage.

Every persistent table has an Alembic migration, tenant context/RLS, runtime role
grant and migration test. The full test, type, lint, Compose, dependency-audit and
PostgreSQL/MinIO gates must pass before a GitHub deployment. A deployment never
approves a legal version or turns a reference document into legal evidence.

## Explicit non-goals

- No automatic legal qualification of a document as a claim or lawsuit.
- No personal account, public submission, payment, patient-facing legal response,
  external LLM upload, clinical advice, new trusted source or corpus approval.
- No extraction of identifying text into analytics, fixtures, diagnostics or audit.
- No statement that a lawyer-reviewed corpus or legal answer quality is complete
  until approved, date-applicable versions and reviewed synthetic evaluation cases
  exist for the topic.
