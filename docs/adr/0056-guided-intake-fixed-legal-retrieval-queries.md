# ADR-0056: Plan guided-intake legal searches from fixed topic signals

- **Status:** Accepted
- **Date:** 2026-10-07

## Context

The guided v2 intake in ADR-0049 records ordinary-language facts instead of the
legacy claim, incident, demand and harm labels. The existing legal query planner
read those legacy labels, so a v2 case searched almost exclusively with broad
base phrases even when the user had selected a more specific topic. Passing the
event narrative or affected-service wording to a semantic embedding service
would expose free text and could let a user's wording steer legal retrieval.

## Decision

For cases explicitly marked `GUIDED_V2`, build a bounded, deduplicated query plan
from the base phrases and a narrow allowlist of confirmed closed-choice fields:

- `PERSONAL_DATA` and `MEDICAL_RECORDS` situation areas select the existing
  privacy and patient-record search phrases.
- `OFFERED_REFUND` and `OFFERED_CORRECTION` clinic actions select the existing
  refund and correction search phrases.
- `COMPLICATION_OR_WORSENING` and `HOSPITALIZATION` health signals select the
  existing health-harm search phrase. `UNKNOWN`, `NO_KNOWN_INFORMATION`, an
  `OTHER_CLINIC` visit alone and missing signals do not select it.

All v2 phrases already belong to `_SEMANTIC_SAFE_QUERIES`. Do not incorporate
v2 event narratives, affected-service labels or any legacy fact in that plan.
The legacy planner retains its former behavior for cases without the v2 marker.
These are search candidates, not a determination that a complaint is a formal
claim, that a defect occurred, or that the clinic owes any remedy.

The existing retrieval boundary remains authoritative: only `APPROVED`,
date-applicable legal fragments may be used as evidence. The plan does not
change risk policy, approve documents, or permit an automatic patient response.

## Consequences

V2 cases can find topic-relevant candidates without sending case free text to
external embeddings. A user-selected category alone cannot justify a legal
claim; the evidence and abstention gates still apply. This deliberately leaves
generic incoming-document labels, chronology, `OTHER` values and an unrelated
visit to another clinic without a narrower legal query until a separately
reviewed mapping exists.

## Alternatives considered

- Reuse legacy claim/harm fields by inferring them from v2 answers: rejected;
  this would make a legal qualification that the administrator did not provide.
- Embed the narrative or service names: rejected; free text can contain
  identifiers or prompt injection and is outside the reviewed query allowlist.
- Add new model-generated or web queries: rejected; Legal Core uses deterministic
  retrieval from its approved, date-applicable corpus.
