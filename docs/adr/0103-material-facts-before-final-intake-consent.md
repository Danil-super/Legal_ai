# ADR 0103: Collect material factual clarifications before final intake consent

28 October 2026. The existing guided intake could reach its final summary before
service date/payment and response-purpose questions existed. These questions were
then discovered by report reasoning, producing a second intake after consent.

Core now shares a pure collection plan with Gateway and the existing answer
interpreter. For actual treatment service slots, the plan includes the existing
payment-basis and service-date identities; the incoming case also includes its
global response purpose. These retain Core's existing SOURCE_APPLICABILITY and
ACTION_SELECTION material-question reasons. ADR 0087's temporal verification
requires exact dates for every affected service when a treatment claim depends on
that scope. The event date cannot substitute for a service date.

The plan adds no fact key or legal prerequisite. An explicit UNKNOWN or approximate
date is an attempted answer; automatic navigation and additional factual extraction
skip it. Core's existing risk, evidence and temporal gates still decide whether an
answer is limited. Explicit Back editing remains available. Known urgent reports
retain their existing bypass of ordinary clarifications.

Collection can cover all eleven supported identities without changing the maximum
of three model-requested clarification questions per report batch. Retained recovery
batches preserve their validated identities and order. Source-bound additions can
fill these supported targets from the retained initial narrative during any ordinary
answer, so supplying service facts after the service-selection step does not lose
them. The existing source checks, fill-only semantics and final factual consent
continue to apply; neither planning nor model extraction confirms a fact.

The Agent must not serialize the full extraction target universe into the retained
three-question report batch. Larger preflight universes are re-derived from the
actual factual context; old bounded recovery batches keep their existing restoration.
Real-client regressions cover primary answers and source-bound additions for two
and five services without widening the report batch contract.

Synthetic regressions reproduce the premature summary, reuse UNKNOWN attempts,
preserve service/date scope and urgent bypass, and cover five services and explicit
Back editing. They do not establish live-model latency or legal readiness.

Complete short answers to the current bound question need no semantic model call:
exact «да/нет» safety signals, an explicit ISO or DD.MM.YYYY event/service date,
and the fixed payment labels «платно», «ОМС», «по ОМС». Only the whole input is
recognized; narratives, relative/ambiguous dates, invalid/future dates and replies
to another question retain the existing model route. Privacy, factual-context and
retained-source validation run first, then Core's current-question/scope/date value
validator runs before any literal proposal is returned. The whole source is its
anchor; the proposal changes no draft or confirmed fact, contains no added facts
and still requires the existing final factual-summary consent. The previous exact
amount path, including its UNKNOWN behavior, is unchanged. This shortcut does not
change authorization, operation leases/revisions, models/high or call budgets.

A batched clarification prompt also permits partial or out-of-order replies. When
only a secondary target is answered, the primary remains UNRESOLVED with null value,
empty primary spans and its bounded reason; a nonempty factualProposal can retain
independently source-bound additions. The simplified flow stages those additions
provisionally and asks the still-unanswered primary. Ordinary acceptance of an
UNRESOLVED primary remains forbidden. No UNKNOWN answer is manufactured.

Core revalidates each candidate against the reserved source IDs/hashes, current
allowed targets, actual service scope and fill-only rules at model completion,
proposal reads and staging. Existing actor ownership, lease, revision, proposal
audit and idempotent staging receipts remain authoritative. Empty proposals cannot
stage anything. The two-source bundle still contains only the initial narrative
and current reply; staged structured values preserve earlier facts without adding
raw reply history. Final factual-summary confirmation remains the single consent.
Real PostgreSQL and PTB tests cover a secondary date followed by payment, including
an explicit unknown date, replay and forged/stale/foreign proposals.

The new literal shortcuts yield to the rich interpreter when the retained initial
description can still supply other planned material targets. A short payment reply
must not prevent reuse of a date or response purpose already in that description.
Only the final remaining target, or a reply without such retained work, takes the
shortcut. Ordinary legacy UNRESOLVED receipts may retain bounded primary anchors;
only the new secondary-only proposal requires empty primary spans. Such ordinary
receipts remain readable and cannot be staged or accepted as facts.

Deploy compatible Core, Agent and Gateway readers together. To pause production,
disable the existing producer flags on a compatible reader; do not roll back to a
reader that rejects the retained secondary-only receipts. No database downgrade or
automatic source approval is part of this change.
