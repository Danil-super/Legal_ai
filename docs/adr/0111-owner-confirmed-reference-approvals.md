# 0111: Owner confirms lawyer-provided reference scenarios

Date: 2026-11-02. Status: accepted for the owner's explicit request to approve
67 lawyer-provided cases. This is not approval of normative legal versions.

The owner imported the examples and explicitly confirms their review by a lawyer.
Ordinary independent review cannot truthfully represent this instruction: the owner
is the recorded importer and is not the lawyer who prepared them. Preserve ordinary
`APPROVE_FOR_EVALUATION` requirements, including the distinct-reviewer restriction.

Use the existing snapshot-bound `POST /v1/reference-evaluations/{id}/review` with a
new distinct `OWNER_CONFIRMED_APPROVAL` decision and exact explicit note
`LAWYER_PROVIDED_AND_CONFIRMED`. Require an active clinic owner with CONTRIBUTOR
access and current entitlement. No REVIEWER grant or impersonated lawyer action is
created. Recheck authorization and original content deadline after all flush waits.
Every supplied source snapshot must still match under source locks. Existing
idempotency, retention, audit and tenant boundaries apply. Approval is per exact
version; it does not approve future revisions.

The append-only review guard additionally checks owner membership, active user,
contributor grant, active entitlement and explicit attestation for this decision.
Independent review checks remain unchanged. The additive migration preserves
function ownership and dynamically identifies the existing decision constraint.
Downgrade refuses to discard retained attestations. Runtime rollback retains this
schema and history; withdrawing an example uses the existing RETIRE operation.

Analogy selection accepts either independent review or this explicit owner
attestation. The immutable approval event remains in private provenance, while
both model profiles receive only the bounded de-identified analogy projection.
Examples provide approaches, not legal evidence, patient facts or risk-policy
instructions. Current law still requires approved date-applicable fragments and
Core verification. The current compact scenario includes the supplied short
answer; the complete workbook fields remain in private source attachments. This
change does not claim that full attachment text enters model context or that the
imported ABSTAIN starting route is a lawyer-labelled expected route.

No fine-tuning job is started: the configured gpt-6.1-sol model does not support
fine-tuning. The mechanism is approved-example retrieval in both model profiles;
it does not change model weights or certify the correctness of generated prose.
