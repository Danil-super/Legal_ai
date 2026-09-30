# Specification: de-identified historical reference cases

## Status and scope

The owner requested this workspace on 2026-09-30 so that the owner and the
authorised lawyer can load real historical examples for controlled evaluation.
It is not a patient-facing intake, a legal corpus import, a training feed, or a
mechanism for changing a risk policy.  The workspace accepts only **de-identified**
historical cases.  A file or message that contains a direct identifier is rejected
before storage; raw patient records, images capable of identifying a patient and
unredacted medical documents remain out of scope.

The first production grants are deliberately narrow: the owner (`692209811`) is a
contributor; the lawyer (`208067135`) is a contributor and reviewer.  Grants are
stored against the resolved active clinic membership, not an ID supplied by a
Telegram callback.  The obsolete membership `880559710` receives no grant.

## User flow

The Telegram command/button opens only after Legal Core reports a contributor or
reviewer grant.  An unauthorised user receives the same non-enumerating denial as
for an absent workspace.

1. Choose one of the existing seven material groups.  This is a retrieval-coverage
   tag, not a legal qualification or a claim that the group contains sufficient
   law.
2. Give the analysis date and choose the expected safe handling: abstain,
   hand to a lawyer, or produce an internal evidence-bound draft.  This is a
   comparison expectation, never a patient answer.
3. Write a short de-identified factual scenario and optionally upload one
   de-identified TXT, PDF or DOCX file (15 MB maximum).  The server repeats MIME,
   signature, parser, size and direct-identifier checks.
4. The contributor reviews a factual recap and submits the package for review.
5. A distinct reviewer can read the protected case, download its source material
   if any, and choose `APPROVE_FOR_EVALUATION`, `CHANGES_REQUIRED` or `REJECT`.
   A reviewer cannot approve a case version they created.

`CHANGES_REQUIRED` returns the case to its contributor; a later revision is a new,
immutable version.  Only `APPROVED_FOR_EVALUATION` versions may be selected by a
future evaluation runner.  That runner stores comparison metadata only and never
changes a legal version, an approval, a risk policy, an LLM prompt or a patient
response.

## Data, security and retention contract

Each tenant-owned row has `clinic_id`, RLS, a composite tenant foreign key and
explicit application-level grant verification.  Metadata endpoints return no
original filename, object key, checksum or scenario text to a non-granted actor.
Raw bytes reside only below the isolated `evaluation-case/` object prefix and are
never placed in the normative corpus, retrieval index, Hermes context, audit
payload, process log, fixture or Telegram callback.

The case version records the review date, coverage group, expected safe route,
de-identified scenario text, immutable checksums and optional attachment metadata.
Review events are append-only and record only the reviewer, decision, version and
a bounded de-identified note.  Every state-changing request uses the existing
idempotency record mechanism with a stable scope and request hash.

Draft, changes-requested and rejected source objects expire after 30 days.  An
approved evaluation source remains until a reviewer retires it; retirement starts
a 90-day deletion period.  Object deletion is storage-first with a lease/retry
queue; metadata is removed only after private storage reports successful deletion.
No public URL or presigned link is issued.

## Explicit non-goals

- No raw patient data, patient-facing submission, public workspace or new
  personal account.
- No automatic legal approval, legal conclusion, recommendation, escalation or
  risk-policy decision from a reference case.
- No use of unapproved legal versions or clinical reference material as legal
  evidence.
- No self-approval and no access based solely on `CLINIC_LAWYER`, `LEGAL_EDITOR`
  or being in the same clinic.
