# Risk v3 preparation and factual intake follow-up

Owner authorisation: prepare/test v3; direct v1 → v3 only after tests and human
LEGAL_EDITOR confirmation. No automatic approval or production activation.

- [x] R1: Strict opt-in v3 parser and hash/threshold/version validation.
- [x] R2: Typed guided-v2 CRITICAL mapping, stable combined reasons, no prose inference.
- [x] R3: Unknown/conflict abstention and exact existing follow-up identifiers.
- [x] R4: Human-only direct predecessor preflight and immutable transactional supersession.
- [x] R5: Authored synthetic gates; both PostgreSQL confirmation paths, tenant denial,
  unavailable evidence and idempotent routing tests.
- [ ] R6: Independent review and project quality gates on current main.
- [x] R7: Implement/exercise v3-compatible operational safe-stop before activation (ADR 0064).
- [ ] R8: LEGAL_EDITOR reviews exact hash/threshold/triggers and authorises activation.

## Concrete next slice for usable ordinary cases

The current guided questionnaire only offers NO_KNOWN_INFORMATION and UNKNOWN,
and collects neither an exact monetary demand nor representative/regulator facts.
Even after laws are approved, those omissions prevent risk clearance. Keep all
existing answers intact; changing the meaning of NO_KNOWN_INFORMATION is invalid.

Owner-authorised next implementation slice: SPEC-factual-safety-intake-v1.md.
Its versioned factual envelope avoids asking the clinic to classify legal harm,
formal claims or deadlines. The candidate risk capability and new internal
reported-event triggers must be present in the exact LEGAL_EDITOR review payload.
The candidate now includes this explicit optional capability (ADR 0066), disabled
unless included in the exact human-reviewed payload.

- [x] F1: Exact JSON envelope and partial durable-draft validation; no invented NO.
- [x] F2: Hash-covered factual candidate capability and separate human review flag.
- [x] F3: Seven ordinary questions, exact RUB money, durable Back/resume and summary.
- [x] F4: Known urgent reports can skip unanswered questions as explicit UNKNOWN;
  both real confirmation endpoints route HIGH/CRITICAL before evidence/model work.
- [x] F5: End-to-end LOW/MEDIUM through real Core/orchestrator/worker/verifier/report
  and PDF using synthetic external model responses, plus PG draft/tenant tests.
- [ ] F6: Independent review and release gates on rebased current main.
- [ ] F7: Separate correction/new-draft workflow for already finalized cases;
  the present implementation does not mutate or unlock their immutable snapshots.
- [ ] F8: Human LEGAL_EDITOR activation and lawyer benchmark correctness review.

Verification on the isolated feature branch: 1210 unit tests pass, 150 PG tests
are skipped in the unit run; targeted disposable-PG factual+report tests: 9 pass.
Ruff and strict Core mypy pass. External provider connectivity/real-law correctness
are not claimed by these authored synthetic tests.

Implementation outline:

1. Ask a short factual safety follow-up before final confirmation: “Сообщал ли
   пациент об ухудшении здоровья или осложнении?” and “Сообщалось ли о
   госпитализации?” Each has explicit “да / нет / неизвестно”; no medical or legal
   conclusion is requested. Record the actual selected answer and its provenance.
2. Ask whether a lawyer/representative contacted the clinic, whether a court or
   authority document actually arrived, and whether referral to an authority was
   mentioned. Record yes/no/unknown without asking the clinic to qualify a claim.
3. Ask what the patient requests in ordinary words and, if money, the exact RUB
   amount or “неизвестно”. A generic FORMAL_DOCUMENT stays unclassified; deciding
   it is a formal claim needs the evidence-backed qualification layer or a lawyer.
4. Show these factual answers in the confirmation summary. Missing/unknown answers
   lead to a specific follow-up; an explicit urgent signal routes immediately.
5. Keep finalized snapshots immutable. A later correction/clarification requires
   an audited new snapshot/revision flow, not editing the old case facts in place.

Acceptance tests must prove explicit negative facts allow evidence-verified LOW;
refund/compensation below 50k gives MEDIUM; exact 50k gives HIGH; hospitalization
or court gives CRITICAL; unknown stays unavailable; generic “document” is not
inferred as a claim; contradicting health facts stay blocked with urgent routing;
old draft resume and both finalization entry points work; no cross-tenant access.

The owner has authorised preparing and testing this workflow; do not re-request
permission for routine implementation. The present v3 payload has not been
activated, so the explicit extension can be included in its candidate before
the human review. LEGAL_EDITOR confirmation remains required for activation;
changing an already approved payload would require a new policy version.
