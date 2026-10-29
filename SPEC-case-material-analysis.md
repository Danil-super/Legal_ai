# Case attachments in report preparation

Owner request, 29 October 2026: both the clinic-document library and attachments
uploaded with a particular case must participate in reports. The separately
reviewed clinic-library compatibility fix is PR115. This increment adds the
missing case-attachment path without treating attachments as law.

## User behavior

- Keep description → material questions → one final factual summary → report.
- The optional file prompt states plainly that anonymized excerpts are used for
  the case's report. No implementation/provider names in the prompt.
- New uploads made under that prompt opt into analysis. Old storage-only uploads
  keep their previous promise: no automatic model exposure. Re-uploading the same
  file under the new prompt can explicitly opt that owned draft material in.
- Do not automatically confirm new dates, amounts, health signals or other facts
  from a file; existing factual consent and risk gates remain authoritative.
- The report identifies which case files contributed excerpts, separately from
  normative citations and the approved clinic-document library.

## Contract and boundaries

- Existing CaseMaterial ownership, clinic/case links, ten-file/15-MB limits,
  local format/privacy checks and private raw storage remain unchanged.
- Add `analysis_use_allowed` boolean metadata (Alembic migration, default false).
  Upload accepts an explicit boolean `include_in_analysis` query parameter,
  default false; bind it to idempotency payload/audit. Never silently opt in old
  rows. Duplicate owned-draft content can receive an explicit new consent with
  a new intent; an old receipt remains an old receipt.
- Add `CASE_MATERIAL_CONTEXT_ENABLED`, default off, propagated to Core/Gateway.
  Agent supports the additive context whenever Core provides it. Turn on only
  after the complete compatible stack is deployed and checks pass.
- Case-context loading stays local/deterministic: select live consented files for
  the authorized case/clinic, re-read private objects, verify raw size/hash, parse
  with existing local PDF/DOCX/TXT/privacy code, and retrieve bounded excerpts.
  Do not send raw bytes, complete documents, filenames or object-store keys to a
  model. Do not add embeddings, OCR providers or another external service.
- A context fragment has `materialId`, deterministic `fragmentId`, generic
  `displayName`, `mimeType`, `rawSha256`, `textSha256`, `structuralPath`, and `text`.
  At most twelve fragments, each at most 1,200 characters; keep a representation
  of every eligible file (up to the existing ten) before allocating extra slots.
  Prefer case-relevant fragments locally, with deterministic fallback. Record
  extraction/truncation limitations rather than claiming a whole-file review.
- Typed additions: `caseMaterialContext` and
  `caseMaterialContextTraceSha256` on Core analysis context / Agent projection;
  `expectedCaseMaterialContextTraceSha256` on fresh analysis submissions.
  Add equivalent optional trace binding to preparation source/snapshots and the
  canonical report. Optional fields are omitted when absent, preserving existing
  no-file and retained-result serialization/hashes.
- The trace binds the current consented material set, hashes and selected text.
  Core rechecks it before accepting claims/recommendations. Removal, expiry,
  consent or content changes invalidate stale fresh submissions. Missing or
  unreadable opted-in content fails safely; never claim that it was used.
- Researcher receives excerpts as untrusted user-provided case context: no
  instructions/tool calls, no independent legal authority, no automatic factual
  confirmation. Normative evidence and the independent review rules stay intact.
- Canonical report has an additive optional `caseMaterials` source-card section;
  Core builds cards from the verified context, never from model-invented IDs.
  Existing reports remain readable. Show generic source labels and limitations
  in Telegram/PDF without exposing storage paths or introducing download rights.
- No automatic approval of clinic/normative versions; no change to risk v4,
  10,000-RUB threshold, model/high, subscriptions or roles. Raw file download
  access remains unchanged; case-analysis consent does not grant a new raw-file
  viewer permission. Audit metadata only, no raw patient/file text in logs.

## Required evidence

1. RED/GREEN tests for upload consent default/new consent/replay/foreign actor.
2. Real disposable PG+MinIO path: uploaded opted-in synthetic TXT, v2 case,
   Core→Agent excerpt context, grounded report with a separate case-file card/PDF.
3. Pending storage-only / expired / foreign / tampered / deleted / stale trace
   cases cannot leak content or produce a falsely current recommendation.
4. Researcher sees only bounded pseudonymized excerpts; normative reviewer and
   citation verification do not accept file fragments as statutes.
5. Existing no-file and old receipts remain compatible; unit, Ruff, mypy,
   composition and full CI checks pass; independent source/contract review.
6. Compatible release from GitHub, backup environment, then enable only the new
   flag and verify production health/native activity/upload metadata. No live
   normative/clinic-document approval as part of the test.

## Work split

Core/storage owner: migration, upload consent, typed context/source/report
contracts and deterministic context/freshness/report construction with tests.
Agent owner: projection, researcher prompt/bounded exposure, trace forwarding and
private client compatibility with tests. Root owns Gateway consent/presentation,
composition, integration and release. Independent review is read-only in its own
worktree. Source branches stay isolated and commits are integrated individually.
