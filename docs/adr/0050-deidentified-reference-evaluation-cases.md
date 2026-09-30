# ADR-0050: De-identified historical cases use a separate evaluation workspace

- **Status:** Accepted
- **Date:** 2026-09-30

## Context

The owner needs real historical examples to measure whether an evidence-gated
system routes and drafts cases safely after the legal corpus is reviewed.  Existing
ordinary case materials are short-lived and owned only by their uploader.  The
legal corpus and its source-review inbox are intentionally immutable legal-source
systems.  Neither is an appropriate place for historical case examples.

The owner has authorised a workspace for the owner and one lawyer, but not raw
patient-data processing, external model processing or a wider clinical role grant.

## Decision

Create a tenant-scoped reference-evaluation workspace with explicit contributor
and reviewer grants.  It stores only de-identified historical examples, with a
versioned source package and append-only review events.  Direct identifiers are
rejected before an optional PDF, TXT or DOCX reaches isolated private object
storage.  A reviewer cannot approve their own version.

`APPROVED_FOR_EVALUATION` means only that a de-identified example is eligible for
a future controlled comparison.  It is not a legal-source approval, a change to
Legal Core truth, a risk-policy update, a recommendation or authority to send a
message to a patient.  Raw package bytes are excluded from retrieval, Hermes and
all model contexts.

Draft/rejected/revision-requested packages expire in 30 days.  Approved packages
are retained only until a reviewer retires them; retirement begins a 90-day,
storage-first deletion workflow.  The release provisions exactly the owner as a
contributor and the named lawyer as contributor/reviewer through production data,
not hard-coded IDs in source code.

## Alternatives considered

- Put examples into the legal corpus: rejected because a fact pattern is not
  normative evidence and would compromise approval/retrieval guarantees.
- Treat them as ordinary case attachments: rejected because its uploader-only,
  30/90-day lifecycle cannot support a reviewer evaluation package.
- Send the originals to Hermes: rejected because the model is not the legal truth
  boundary and processing raw historical materials was not authorised.
- Give all lawyers access: rejected because the user requested two named actors;
  broad clinic roles cannot replace an explicit case-workspace grant.
