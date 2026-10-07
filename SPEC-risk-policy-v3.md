# Specification: deterministic risk policy v3 for guided intake

## Status, authority and assumptions

**Implementation preparation authorised by the owner; production activation requires LEGAL_EDITOR confirmation.**
The owner approved the direction: use only confirmed guided-v2 facts for new v2
signals, route explicit hospitalisation or a court/authority document to the
clinic lawyer/owner, ask for missing critical facts or abstain, preserve v1/v2
policy content, and keep the approved 50,000 RUB HIGH-demand boundary unchanged.
After read-only production inspection on 2026-10-07, the owner also authorised a
**direct v1 → v3** transition after tests and explicit legal-editor confirmation;
v2 must not be activated as an intermediate step.
This spec narrows that direction into a reviewable contract. It does not approve
any legal source or policy row.

Confirmed production predecessor (read-only inspection reported on 2026-10-07):
the only `dental-risk` policy row is version 1, status `APPROVED`, payload schema
`risk-policy.v1`, threshold `highDemandThresholdKopecks=5000000`. A fresh
pre-activation read must recheck these facts and its stored content hash; this
snapshot is evidence of current state, not permission to skip the later check.

Assumptions requiring confirmation before implementation:

1. A selected v2 `HOSPITALIZATION` or `AUTHORITY_OR_COURT_DOCUMENT` is an
   administrator's **report**, sufficient for urgent internal routing without
   verifying the underlying document or making a legal finding. As in policy v2,
   each maps to `CRITICAL`, not merely `HIGH`.
2. The current guided-v2 vocabulary is authoritative for v3. Free text, file
   names, uploaded bytes, model output and legal-source metadata do not create
   risk signals.

Sources: `SPEC-guided-case-intake-v2.md`, ADR 0024, `risk_engine.py`,
`case_wizard.py`, `guided_case_intake_v2.py`, `risk_policy_approval.py` and
`risk_policy_repository.py` at the branch base. The existing v2 adapter writes
`HEALTH_CONSEQUENCE_SIGNALS` and `INCOMING_COMMUNICATION`; the existing engine
reads legacy `HOSPITALIZATION` and `REGULATOR_OR_COURT`. That gap is the reason
for a versioned v3 rule. The guided intake remains `dental-case-intake.v2`;
policy v3 does not rename or reinterpret the intake schema.

## Objective and success condition

After an authorised clinic user confirms a guided-v2 case, Legal Core identifies
the two explicit urgent signals deterministically and creates the existing,
tenant-scoped lawyer escalation even when approved legal evidence or an LLM is
unavailable. Unknown critical facts lead to a precise follow-up or an unavailable
assessment, never a LOW clearance. Existing v1/v2 cases and assessments remain
interpretable under the policy version recorded with them.

## Scope and fact contract

Only a server-frozen, confirmed fact snapshot is eligible. The gateway emits
typed facts with `sourceType=USER_STATEMENT`; Legal Core validates exact types and
closed enum values. v3 reads the following v2 keys directly, without writing
fabricated legacy facts:

| Confirmed v2 fact | Exact value | v3 risk meaning | Action |
|---|---|---|---|
| `HEALTH_CONSEQUENCE_SIGNALS` | contains `HOSPITALIZATION` | Explicit report of hospitalisation; `CRITICAL`, reason `HOSPITALIZATION_REPORTED` | Create/reuse lawyer escalation immediately. |
| `INCOMING_COMMUNICATION` | `AUTHORITY_OR_COURT_DOCUMENT` | Explicit report of a court/authority document; `CRITICAL`, reason `AUTHORITY_OR_COURT_DOCUMENT_REPORTED` | Create/reuse lawyer escalation immediately. |
| `HEALTH_CONSEQUENCE_SIGNALS` | `UNKNOWN` | Consequences not established | Ask the health-signal question; if unavailable, abstain. |
| `HEALTH_CONSEQUENCE_SIGNALS` | `NO_KNOWN_INFORMATION` | No information available; **not** an affirmative `NO` about harm or hospitalisation | Ask for clarification if required for full risk; never grant LOW from this value. |
| `INCOMING_COMMUNICATION` | `UNKNOWN` | Kind of incoming communication not established | Ask what arrived; if unavailable, abstain. |
| `INCOMING_COMMUNICATION` | `FORMAL_DOCUMENT`, `MESSAGE_OR_REQUEST`, `COMPLAINT`, `OTHER` | Description only; no automatic legal classification as claim, lawsuit or regulator action | No new HIGH/CRITICAL trigger; assess other explicit facts or abstain. |
| `HEALTH_CONSEQUENCE_SIGNALS` | `COMPLICATION_OR_WORSENING`, `OTHER_CLINIC`, `OTHER_CONSEQUENCE` | A reported fact, but no owner-approved new HIGH mapping | Keep visible for human clarification; do not downgrade to `NO` or infer legal harm. |
| `INCOMING_SOURCE_STATUS`, `CASE_MATERIALS_STATUS` | any valid value | Presence of material says nothing about authenticity, filing, injury or legal status | No risk-level change. |

`HEALTH_CONSEQUENCE_SIGNALS` is a set. Existing guided-v2 validation keeps
`UNKNOWN` and `NO_KNOWN_INFORMATION` mutually exclusive with affirmative
signals. Exact matching is required: descriptions or lexical variants in
`EVENT_SUMMARY` never count. A selected court/authority document routes even when
`INCOMING_SOURCE_STATUS=NOT_ATTACHED`; the card must say it is **reported and
unverified**. `FORMAL_DOCUMENT` cannot be promoted to `FORMAL_CLAIM=YES`.

Legacy v1 facts retain their existing semantics under v3: `HOSPITALIZATION=YES`
and `REGULATOR_OR_COURT=YES` are CRITICAL; `LAWYER_CONTACT=YES`,
`FORMAL_CLAIM=YES`, `HARM_CLAIMED=YES`, and a valid RUB `DEMAND_AMOUNT` at or above
5,000,000 kopecks are HIGH. Existing MEDIUM triggers remain unchanged. A v2
selection never silently supplies missing legacy `NO` answers. The v3 threshold
is inclusive and fixed at 5,000,000 kopecks; 4,999,999 does not trigger HIGH by
amount alone. No amount is inferred from prose.

## Deterministic precedence and unknowns

1. At case confirmation, early triage examines all **affirmative, typed** v2
   signals above and any independently confirmed legacy signals. CRITICAL wins
   over HIGH. A known affirmative trigger wins over an unrelated `UNKNOWN` for
   **routing only**: the lawyer must receive the case immediately. For two
   CRITICAL signals, preserve both reason codes in a stable order:
   `HOSPITALIZATION_REPORTED`, then the applicable court/authority code. For
   that second dimension, use `AUTHORITY_OR_COURT_DOCUMENT_REPORTED` when the
   guided-v2 choice is present; otherwise use the legacy
   `OFFICIAL_REGULATOR_OR_COURT_SIGNAL`. If both representations are positive,
   emit only the guided-v2 code for that dimension.
2. After early routing, contradictory confirmations (for example guided-v2
   hospitalisation alongside a separately recorded legacy `HOSPITALIZATION=NO`)
   require a correction prompt and block a final LOW/MEDIUM assessment. The
   positive signal is not retracted automatically; the escalation remains
   available for human review. Corrections create a new fact snapshot and new
   assessment, preserving history.
3. If there is no affirmative HIGH/CRITICAL trigger, unresolved safety facts
   yield `UNAVAILABLE` with a stable missing-fact reason and exact follow-up
   question. For guided-v2, `UNKNOWN`, `NO_KNOWN_INFORMATION`, an absent or invalid
   required health answer, or `INCOMING_COMMUNICATION=UNKNOWN` cannot be treated
   as negative. `OTHER_CONSEQUENCE` and `OTHER_CLINIC` alone do not establish the
   absence of hospitalisation. The system must not issue a LOW or MEDIUM level
   from the current guided-v2 fields alone while legacy safety questions remain
   unanswered. Use `HEALTH_CONSEQUENCE_SIGNALS_UNKNOWN` with existing question ID
   `health_consequence_signals` for unresolved health status and
   `INCOMING_COMMUNICATION_UNKNOWN` with existing question ID
   `incoming_communication` for an unknown communication kind. If the present
   questionnaire cannot obtain an explicit negative safety answer, abstain and
   offer the authorised human review path. A later explicit answer may allow
   re-evaluation.
4. Full legal analysis remains evidence-gated. If applicable `APPROVED` evidence
   or verification is absent, return `UNAVAILABLE` / `LEGAL_EVIDENCE_UNAVAILABLE`
   as appropriate, while retaining the separate early HIGH/CRITICAL escalation.
   Unknowns and missing evidence never erase a known urgent routing decision.

All risk records include the exact policy ID/version, frozen fact-snapshot hash,
reason codes and tenant `clinic_id`. Repeating early/full assessment of the same
case, snapshot and policy must reuse the existing escalation, including when
evidence later becomes available. No raw patient information or medical-document
content appears in risk reasons, logs, audit or synthetic fixtures.

## Policy versioning, migration and activation

Introduce a distinct `risk-policy.v3` payload for `dental-risk.v3` with an
explicit guided-v2-signal capability flag, `earlyTriageEnabled=true`, and
`highDemandThresholdKopecks=5000000`. The parser accepts only the exact v3
shape, validates the version/flag/threshold combination, and continues to parse
v1/v2 exactly as before. Reject unknown fields and unsupported versions. The
approval path must run a v3-specific synthetic regression gate and require the
existing active `LEGAL_EDITOR` attestations; it must never turn v3 on solely by
deploying code or merging a branch.

The proposed canonical v3 content is:

```json
{
  "schemaVersion": "risk-policy.v3",
  "highDemandThresholdKopecks": 5000000,
  "earlyTriageEnabled": true,
  "guidedV2ExplicitSignalsEnabled": true
}
```

No implicit default enables the new flag. The content hash covers exactly this
canonical object, and a policy with version number below 3 cannot use this
schema. An implementation must preserve v1/v2 validation and outputs; it may
not switch their behaviour based on the active v3 parser.

The existing `risk_policy_versions` and `risk_policy_events` tables are expected
to suffice. If implementation changes schema, add an Alembic migration and
PostgreSQL/RLS tests. Do not edit v1/v2 policy payloads or their approval events.
The existing ADR 0024 described v1 → v2 activation; the new ADR must explicitly
supersede that deployment sequence for this direct v1 → v3 transition, while
preserving ADR 0024 as history.

Activation is a separate, explicitly authorised transaction **after** all v3
unit, integration, PostgreSQL, tenant, synthetic regression and deployment
compatibility gates pass:

1. Freshly read and lock the sole `dental-risk` predecessor. Require version 1,
   status `APPROVED`, `schemaVersion=risk-policy.v1`, threshold exactly
   5,000,000 kopecks, a matching recomputed/stored SHA-256, and a valid prior
   `APPROVED` event. Require no other approved `dental-risk` policy. If any fact
   differs from the inspected production snapshot, stop; do not quietly choose
   another predecessor or change the threshold.
2. Present the exact canonical v3 payload, its hash, the v1 predecessor and the
   regression results to an active `LEGAL_EDITOR`. Require that person's explicit
   confirmation of incident triggers, unchanged monetary threshold, escalation
   rules and direct v1 → v3 supersession. Deployment or an owner direction alone
   cannot write an approval event.
3. In one guarded transaction, create `dental-risk.v3` as `DRAFT` if absent,
   verify immutable content/hash if it already exists, append `RETIRED` for v1,
   transition v1 to `RETIRED`, append `APPROVED` for v3, and transition v3 to
   `APPROVED`. Abort the entire transaction on any mismatch or concurrency
   conflict. Do not create, approve or retire a v2 row as part of this sequence.
4. Read back the one-active-policy invariant, v1/v3 events and hashes, and
   smoke-test an authored synthetic hospitalisation and court/authority case
   through both confirmation entry points before enabling v3 traffic.

Historical risk assessments and escalations continue to reference their recorded
policy; no backfill or silent reclassification of old cases is allowed. A new ADR
must accompany the eventual critical contract change and document activation,
compatibility and rollback before implementation is merged.

Rollback gates are fail-closed. Before v3 approval, a failed gate leaves v1
`APPROVED` and stops rollout. After v3 approval, the DB guards disallow
`RETIRED → APPROVED`, so neither a manual status edit nor an old application
binary that cannot parse v3 is a valid rollback. A release must provide and test
a v3-compatible safe-stop mode for new analyses: no LOW clearance or patient
draft, no deletion/downgrade of existing escalations, preserved unconfirmed
drafts, and continued authorised lawyer access. New case confirmations that
cannot be safely assessed must fail closed with a clear retry/manual-review
state. Restoration of automated risk evaluation requires a **new**
monotonic policy version (at least v4), separately reviewed by the owner and an
active `LEGAL_EDITOR`, with its own exact payload, regression tests and
transactional v3 retirement/approval events. No replacement version is
pre-approved by this specification.

## Legal, tenant and output boundaries

Legal Core alone decides risk from typed confirmed facts. The LLM may neither
invent a signal nor approve a policy. Risk routing is not a legal conclusion,
deadline calculation, liability admission or proof of a document's authenticity.
Legal claims still require retrieved, date-applicable `APPROVED` evidence and
independent verification. A report/draft stays internal to authorised clinic
users; no patient-facing legal response is sent automatically. HIGH/CRITICAL
uses the existing clinic lawyer/owner workspace and access rules. The gateway
must not accept a client-supplied `clinic_id`; Legal Core sets tenant context.

## Synthetic scenario matrix and acceptance

Use authored, fictional typed facts only. The v3 matrix must verify both early
triage and the later evidence-gated result; the existing v1/v2 packs stay fixed.

| Scenario | Early result | Full-result constraint |
|---|---|---|
| v2 `HOSPITALIZATION`, no legal evidence, other safety facts unknown | CRITICAL + one escalation | Analysis unavailable; no patient draft. |
| v2 `AUTHORITY_OR_COURT_DOCUMENT`, source not attached | CRITICAL + reported/unverified reason | No inferred claim, filing date or deadline. |
| Both explicit v2 CRITICAL signals | CRITICAL; both ordered reason codes | One escalation per case/snapshot/policy. |
| v2 hospitalisation plus legacy `HOSPITALIZATION=NO` | CRITICAL routing | Conflict prompt; no LOW/MEDIUM result. |
| v2 hospitalisation plus legacy HIGH demand of exactly 50,000 RUB | CRITICAL | No downgrade; threshold behaviour also passes its separate boundary case. |
| Legacy HIGH demand 49,999.99 / 50,000 RUB | no amount HIGH / HIGH | Exact inclusive boundary, no prose extraction. |
| v2 `UNKNOWN` communication and health `UNKNOWN` | no early escalation | Exact clarification or `UNAVAILABLE`; no LOW. |
| v2 `NO_KNOWN_INFORMATION` | no early escalation | Unknown status retained; no false `NO`/LOW. |
| v2 `FORMAL_DOCUMENT` with attached file | no new trigger | No automatic `FORMAL_CLAIM` or court signal. |
| v2 `COMPLICATION_OR_WORSENING` or `OTHER_CLINIC` alone | no new approved HIGH trigger | Clarification/abstention pending separate policy decision. |
| Same frozen case/snapshot re-evaluated before and after evidence verification | one escalation | Append-only assessment trail with same policy identity. |
| Existing v1/v2 policy and synthetic cases | unchanged | Historical behaviour and content hashes stable; v2 is never activated in the v1 → v3 rollout. |
| Production v1 → v3 activation preflight | v1 only, approved v1 schema, 5,000,000 kopecks, matching hash/event | Mismatch leaves v1 active; no v2 intermediate state. |
| Failed gate before approval / critical defect after approval | v1 stays active / v3-compatible safe-stop | No retired-row reactivation, LOW clearance or lost escalation. |

Acceptance requires deterministic unit tests for the mapping, precedence,
unknowns and threshold; API/PostgreSQL tests for tenant isolation, approved-only
policy loading, human approval/retirement, immutable events, idempotent
escalation and both case-finalisation entry points; and regression tests for
legacy v1/v2 behaviour. The new reason codes and prompt identifiers need a
contract test and a safe Telegram label. Run after project bootstrap:

```bash
python -m pytest
python -m ruff check .
python -m mypy services/legal_core/src
docker compose config --quiet
```

Implementation locations: Legal Core risk policy/engine, early-triage
persistence and report contracts under `services/legal_core/src/legal_core/`;
typed v2 intake mapping under `services/gateway/telegram/src/telegram_gateway/`;
synthetic/unit/integration tests under their existing `tests/` directories;
the contract ADR under `docs/adr/`. Follow existing typed enums, stable uppercase
reason codes and fail-closed validation. This spec adds no executable code.

## Non-goals

- No change to the guided-v2 questionnaire, intake schema, personal bot, new
  personal-data category, external service, LLM provider or legal corpus.
- No automatic HIGH classification for complication, another clinic, other
  consequence, a generic formal document, or a free-text mention of an "иск".
- No new monetary threshold, legal classification, automatic normative-version
  approval, patient-facing send, or bulk re-scoring of historical cases.

## Open questions for owner review

1. Should an explicit guided-v2 `COMPLICATION_OR_WORSENING` become a new HIGH
   signal in a later policy version? Current v3 leaves it for clarification and
   human review; this requires a separate risk-policy decision.
2. What exact follow-up wording should distinguish `NO_KNOWN_INFORMATION` from
   an explicit statement that hospitalisation did not occur? The existing
   `health_consequence_signals` question ID can ask for clarification, but the
   current v2 questionnaire offers no explicit `NO` value. v3 must abstain if
   that distinction cannot be established without a separately approved intake
   change.
3. Which exact safe-stop control and on-call procedure will be exercised before
   release, and what separately reviewed version would restore automated risk
   evaluation after a v3 defect? The new ADR must name these operational details;
   the fixed gates above forbid reactivating retired v1 or skipping human review.
