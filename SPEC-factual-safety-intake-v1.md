# Factual safety follow-up: factual-safety-intake.v1

Status: owner-authorised implementation slice; candidate risk-policy integration
must pass tests and the existing LEGAL_EDITOR review before activation. This
document does not approve a policy, a legal source or a patient's document.

## Problem and outcome

Guided-v2 asks what happened in ordinary language, but it cannot explicitly record
the negative/unknown facts required for LOW/MEDIUM assessment. v3 correctly routes
reported hospitalisation/court documents while refusing to infer negative answers
from NO_KNOWN_INFORMATION. A lawyer's approval of the corpus alone cannot clear
these missing facts.

Add a short factual follow-up before the final user confirmation. Users describe
events and patient requests; they never classify a legal claim, assess legal harm,
select a response deadline or decide which law applies. The existing seven
story/material/summary steps remain the main intake. Ask only facts that have not
already been independently and explicitly confirmed. Free-text/model suggestions
may be shown as candidates, but no YES/NO is recorded without user confirmation.
Absence of an answer is UNKNOWN. A known urgent signal continues early routing
even when the user cannot complete the follow-up.

## Versioned fact envelope

Introduce one typed factual-safety envelope, stored with the confirmed case facts
and included in their existing canonical snapshot hash. Use a distinct fact key
`FACTUAL_SAFETY_SCREENING`, value type JSON, and exact schema identifier
`factual-safety-intake.v1`. Do not write fabricated HARM_CLAIMED, FORMAL_CLAIM or
other legacy answers. New fields contain user reports, not findings of fact.

All fields are required in a confirmed envelope; UNKNOWN is explicit. For draft
resume, missing fields remain unanswered and are prompted, without migrating an
old user's answer in place. Reject extra keys and wrong versions/types.

| Field | Closed values | User-facing factual question |
|---|---|---|
| `healthDeteriorationReported` | YES / NO / UNKNOWN | «Пациент сообщал об ухудшении состояния или осложнении после лечения?» |
| `hospitalizationReported` | YES / NO / UNKNOWN | «Пациент сообщил о госпитализации в связи с этой ситуацией?» |
| `representativeContact` | YES / NO / UNKNOWN | «С клиникой уже связывался юрист или представитель пациента?» |
| `writtenRequirementsReceived` | YES / NO / UNKNOWN | «Получено письменное требование вернуть деньги, исправить лечение или совершить другое конкретное действие?» |
| `authorityOrCourtDocumentReceived` | YES / NO / UNKNOWN | «Клиника получила документ от суда или государственного органа по этой ситуации?» |
| `authorityReferralMentioned` | YES / NO / UNKNOWN | «Пациент сообщал, что обратится в государственный орган?» |
| `moneyRequested` | YES / NO / UNKNOWN | «Пациент просит вернуть или выплатить деньги?» |
| `amount` | exact Money / UNKNOWN / NOT_REQUESTED | For YES only: «Какую сумму он просит? Укажите рубли или выберите “Неизвестно”.» |

NO means the user explicitly confirms the absence of this **reported event**.
It does not establish medical causation, lack of an actual injury or authenticity
of a document. Button text is «Да / Нет / Не знаю». Existing
NO_KNOWN_INFORMATION remains unchanged and does not supply any envelope answer.

For moneyRequested=NO, amount must be NOT_REQUESTED. For YES, amount is either
Money in RUB with integer positive amountKopecks, or UNKNOWN. UNKNOWN moneyRequested
requires UNKNOWN amount. No float, bool-as-number, negative amount, other currency
or lexical amount extraction is accepted. The gateway parses an explicitly entered
decimal RUB amount exactly and shows it in the final confirmation summary.

The envelope has the existing USER_STATEMENT provenance. Immutable confirmation
binds all values and the retained narrative/material pointers into one snapshot.
No raw document is passed to a model by this change. No new category of patient
data, table or external service is introduced; any separate schema/storage change
must use the normal migration and isolation tests.

## Candidate deterministic risk integration

This slice requires an explicit, hash-covered capability in the as-yet-unapproved
v3 candidate. Deploying a questionnaire must not silently change an approved
policy's interpretation. The exact candidate payload and ADR 0061 must be updated
together with parser/approval/synthetic tests. After approval, changing this
interpretation requires a later policy version.

1. Early routing reads confirmed YES events only. Hospitalisation and received
   court/authority document remain CRITICAL; guided-v2 and envelope representations
   of the same positive event are deduplicated. Known affirmative urgent routing
   wins over unrelated UNKNOWN. The money boundary stays 50,000 RUB inclusive.
2. An explicitly reported deterioration, representative contact or written
   requirement is an internal HIGH routing trigger under the candidate policy.
   `writtenRequirementsReceived=YES` uses a new factual reason
   `WRITTEN_REQUIREMENTS_REPORTED`; it does not populate FORMAL_CLAIM or assert that
   a legally sufficient claim has been served. The human policy review must see
   this rule explicitly, rather than infer it from a generic FORMAL_DOCUMENT.
3. Without an affirmative urgent trigger, unresolved safety fields or an unknown
   financial amount cannot yield LOW/MEDIUM. Return exact bounded missing-fact
   identifiers for the unresolved envelope fields. Unknown amount blocks
   classification because it could cross the unchanged HIGH boundary.
4. Confirmed NO answers resolve their own dimensions independently. They never
   overwrite or reinterpret an earlier NO_KNOWN_INFORMATION/UNKNOWN selection.
   A positive guided health/court selection alongside a negative envelope answer
   is a confirmation conflict: preserve urgent routing, ask a correction and
   block the final automated legal assessment.
5. With resolved safety facts and no HIGH/CRITICAL trigger, a monetary request
   below the threshold or reported referral threat yields MEDIUM under existing
   internal review semantics. Remaining current MEDIUM document triggers remain.
   Otherwise LOW is possible only after applicable approved evidence and claim
   verification. No legal recommendation or deadline comes from this envelope.
6. A generic uploaded document or FORMAL_DOCUMENT stays legally unclassified.
   Any missing qualification needed to support a legal claim must be resolved
   from approved evidence and the actual confirmed facts, or sent for human
   clarification. Risk routing alone never authorises a legal assertion.

Historical guided-v2 snapshots without this envelope stay under their recorded
policy and missing-fact behaviour. Existing v1/v2 payloads are unchanged. A new
confirmed envelope may be evaluated only by a policy that explicitly supports
its version. LOW/MEDIUM can never be produced by a backwards-compatible default.

## UI and persistence

Every follow-up has Back and Main Menu; go back to the exact previous question.
Restoring an old draft adds unanswered questions, without prefilling NO. Present
factual answers in one short summary; ask the user to confirm the whole situation.
Urgent known positives offer immediate confirmation and lawyer handoff, with
unanswered facts visible. Do not make the user repeat every detail before routing.

For already confirmed cases, factual corrections require a new audited snapshot
or a new case linked to the earlier one. Never unlock/mutate the old facts. A
revision flow must bind case ID, tenant, expected prior hash and user confirmation;
the superseded report must not remain advertised as the current answer. Until
that flow is implemented, offer a prefilled new draft with the original case
pointer and explicitly require confirmation of the correction.

## Implementation slices and verification

1. Implement and test the envelope validator, exact Money handling and stable
   snapshot hash. Keep this independent of model inference.
2. Add the gateway factual follow-up and summary, with durable resume/back tests;
   Core validates the same closed envelope at both confirmation boundaries.
3. Integrate the explicitly versioned candidate policy and its human-review
   payload, missing-fact identifiers and readable reason labels. Preserve v1/v2.
4. Add a confirmed correction/new-draft workflow; retain immutable snapshots and
   tenant/material ownership throughout. Test stale revision and foreign access.
5. Run both confirmation endpoints and the actual frozen analysis submission
   against disposable PostgreSQL with synthetic approved legal fragments. Never
   treat a healthcheck or corpus approval alone as proof of this path.

Required synthetic scenarios:

| Confirmed inputs | Expected behaviour |
|---|---|
| All safety answers explicit NO, no money request, verified applicable evidence | LOW, internally verified recommendation |
| Monetary request 49,999.99 RUB, safety answers resolved | MEDIUM |
| Monetary request 50,000 RUB | HIGH, one lawyer escalation |
| Monetary request YES with UNKNOWN amount | Clarify amount; no LOW/MEDIUM |
| Health deterioration / representative / written requirements YES | HIGH with its exact reported-fact reason |
| Hospitalisation or received court/authority document YES, corpus empty | CRITICAL routing before model/retrieval; legal answer unavailable |
| Both urgent representations positive | One escalation; stable deduplicated reasons |
| Every critical dimension UNKNOWN or absent | Precise factual questions; unavailable assessment |
| Old NO_KNOWN_INFORMATION without envelope | Still unavailable; no invented NO |
| Guided positive plus envelope NO | Urgent escalation retained; final assessment blocked for conflict |
| Formal-document/file presence alone | No inferred legal claim, harm, deadline or amount |
| Foreign actor / old draft / repeat confirmation / stale correction | Tenant denial / unanswered follow-up / one immutable outcome / explicit conflict |
| SAFE_STOP=1 | Draft and lawyer work usable; no new automated report |

Lawyer review of real benchmark cases remains a separate correctness gate. These
synthetic tests prove mechanics, not the legal accuracy of the 67 submitted cases.
