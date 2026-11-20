# Routine service complaints in dental risk policy v5

The production audit found `WRITTEN_REQUIREMENTS_REPORTED` on both reported
cases. v4 generated a real escalation and a delivered alert for each case.
The service complaint was classified as SERVICE; the additional charge as
FINANCE. Neither record had a monetary demand by the patient.

The owner approved preparing and testing a new policy version on 20 November
2026. v5 retains the inclusive 10,000 RUB threshold and the existing decision
on each positive signal. The narrowed path uses a confirmed SERVICE-only
category, a complaint or message, an explicit negative on a patient monetary
request and no reported positive safety signal or conflicting legacy fact.
Unknowns are retained in the confirmed snapshot. Review scope is not a factual
negative and cannot satisfy a legal claim's required facts.

The Telegram draft response exposes the capability only when Core reads a
valid approved v5. The gateway projects omitted fields as UNKNOWN for the final
review and submission. Editable draft data retains the original partial facts.
Core checks the draft revision and its scope-determining facts under a row
lock. The request's transport revision is excluded from the idempotency hash
so an already confirmed case can replay on either side of activation. Other intake
can use a batch of at most three screening questions. The operator can
explicitly deny the listed events together.
Urgent routing remains deterministic and uses the positive signals before any
model run. The existing exact-date and approved-source gates apply to legal
analysis.

The added `REBOOKED_AFTER_WAIT` choice requires reported waiting and a
new appointment agreed by the patient and clinic. The Core can then choose a fixed,
situation-specific operational message in a verified eligible LOW report. It
contains an apology for waiting and confirms the agreement. Date, time, private
text and legal concessions cannot enter that draft. The human review and
existing patient-draft safety gates apply.

For v5, the existing approval command checks the exact approved v4 payload,
its immutable review event, the new explicit attestation and synthetic risk
regressions before writing approval events. Deployment of the compatible
code and approval of the new policy are separate operations. Keep a
v5-compatible application revision available after any production activation.
