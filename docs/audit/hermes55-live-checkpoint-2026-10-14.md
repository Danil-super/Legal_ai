# Hermes55 live checkpoint — 2026-10-14

All cases below are explicit fictional owner-authorized probes, not patient data.
This records runtime results, including failures; it does not certify legal correctness.

## Release and recovery

The owner authorized request/stale29→55 trial, retaining gpt-6.1-sol/high and all
outer stage65/shared115/worker125/lease180 fences. PR96 initially missed a startup
coupling: assert_tool_free still enforced29. Recreating Hermes exposed both guards
refusing startup. Compatible rollback to PR95/fea949f and recreation restored both
profiles; no DB or approval history was restored/deleted.

PR97 corrects that coupling without disabling the guard. Head5e4c394, merged
main3a106b528e88e34ed952663893dced553ec18ced; PR CI38627886840 and main
CI/deploy38629266571 succeeded. Both Hermes services were then recreated under
the deployment lock, with zero active jobs checked, no dependencies/build/volume
deletion. Actual resolvers for each reported model gpt-6.1-sol, reasoning high,
request55 and stale55. All eight services healthy. The pinned image also passed
offline/read-only/nonroot native bootstrap with synthetic env and no production
volumes: empty tools,55/55, native completion-status guard installed.

Local PR97 regression:2478 passed/zero skips; independent136 focused passed.
55→29 guard mutation triggered three expected failures. No model/provider/key,
source approval, risk/escalation or automatic patient-send change.

## Real Core-persisted positive and actionable clarification

- Original quick case8303bc4d-0698-4fcd-9a8d-b362df6fc716;
  job7882a444-4aac-437b-b93a-aa2fe5259b02 SUCCEEDED. Report
  f16da6a3-432e-4b9a-a2c1-cf57c4bf96ea BLOCKED/FACTUAL_CLARIFICATION_REQUIRED,
  requesting service payment and slot0 date; no legal advice published.
- Actual job-produced report-bound CTA was consumed through the gateway's PTB
  composition; answers PAID and exact2026-10-14 confirmed in a new immutable case
  4b2b1f66-09fa-411f-82f2-2a1078ee33d6. Original case/report remained unchanged.
- Jobb2a86a68-48fe-4a0d-9a47-dcb99f9db37c SUCCEEDED in51.32s;
  reportc4d567b2-9e00-4301-a411-bc04cb7d8b72 READY/PASSED,
  analysisAllowed=true, LOW under dental-risk.v4, no escalation/questions.
  Recommendations AVAILABLE; verified legal conclusion and two evidence cards
  from approved659 point48/point34. Their applicability starts2026-10-08;
  this did not invent a historical edition or an earlier effective date.
- Actual Core PDF download31456 bytes matched stored SHA256, then real Bot API
  delivered it to the owner, message733. Intermediate input/ACK transport in the
  harness is synthetic; queue/Core/Hermes/worker/report/PDF/final delivery are real.

The first operator helper hit AttributeError on analysis:null in a valid blocked
report; a read-only job fetch confirmed successful clarification. The helper was
corrected rather than blaming the application or rerunning that initial job.

## Remaining failures and bounded correction

Guided casef61ca402-a648-461e-ae94-7b893d245355 has a date in narrative but no
explicit slot-bound date answer. Joba03ad2ab-b4bb-4a86-aa7a-eb8092c20f6e
SUCCEEDED57.39s but correctly blocked SERVICE_DATE_BINDING_UNKNOWN with no
actionable question; PDF28297 bytes checksum verified. It is not a positive.

An UNKNOWN successor41833105-323e-4935-a7cc-162b1d48ca5e produced failed job
475aac77-9c01-43ac-ad03-027e8b83fed2/CLARIFICATION_REQUEST_NOT_APPLICABLE:
the model repeated an attempted question. No invalid result was accepted.

PR98/headc91e4dd69a7239e799b375e1ec4e2f39a7b07376 fixes the narrow date
handoff and ambiguous UNKNOWN prompt (ADR0089). Core appends only missing date
slots for an actual SERVICE_DATE_BINDING_UNKNOWN; same max3, no inferred dates,
no repeated attempted identities or invalid-request filtering. Other blockers,
including ANALYSIS_CLARIFICATION_ANSWERS_UNKNOWN, remain deliberately excluded.
Local2494 passed/zero skips, focused real-PG108 passed, Ruff and139-source mypy
passed. Independent117 passed/5PG skips, mixed-slot proof and exact-trigger
mutation; no Required/Critical findings. PR CI38633518562 passed.

Read-only diagnostics executed this exact candidate reasoning module only in a
temporary process, not production workers. Real unchanged provider/high/55:
UNKNOWN returned zero claims/reviews/questions; guided first failed protocol,
then after a pause returned one claim/review and no questions. No candidate result
was submitted. Sanitized reviewer logs show APIError/HTTP429; catalog access or
these diagnostics do not prove uninterrupted provider availability or Core readiness.

At this checkpoint PR98 is OPEN, release approval requested, not deployed. Live
guided completion/recovery and Core-persisted truthful UNKNOWN negative remain
required after release. The one quick positive is not full legal readiness or
proof of all67 reference cases; those remain DRAFT and outside online reasoning.

## Approval ledger and boundaries

Server read-only snapshot unchanged:54 normative events, sorted full-row equality
digest aa73a7b681b4198193bd63983f17eeb2;8 reference-material reviews, digest
a1e8f8cb1acf90e3ade6ed295e8495cd. These MD5 values are equality checks, not legal
signatures. No automated approval, role/auth changes, clinical files sent to a
model, or automatic legal message to a patient occurred.
