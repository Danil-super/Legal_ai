# Dental Legal AI task list

## Pre-report case understanding — PRC, 2026-10-15

- [x] Owner approved SPEC-pre-report-intake-understanding.md on15Oct.
- [x] Preserve isolated UI fix6624084 and original dirty worktree; no deploy claimed.
- [x] Owner approved execution of this plan/tasks after clarifying Hermes' semantic
  role on15Oct ("да, ты правильно меня понял, работай"). No release/legal approval implied.

This extends the existing FC work without closing unrelated tasks. File paths
below are service-relative unless marked root; new paths are planned, not present.
Every implementation task starts RED, ends focused GREEN/quality checks and an
atomic commit. At most five files per task; split if the actual change exceeds it.
Use source PYTHONPATH and venv from the spec; commands below are pytest selections.

### PRC1: typed intermediate contract and storage feasibility gate — complete

- [x] Define opt-in NEEDS_FACTS/LIMITED/READY/HUMAN_REVIEW with exclusive payloads;
  no PDF/report for preparation, no ambiguous legacy null-report payload.
- [x] Prove storage design for bounded early understanding and intermediate
  recovery, including operation fencing, privacy expiry and exact schema needs.
  Existing draft jobs/report FK assumptions may not be bypassed with fake rows.
- [x] Record schema/rollout/rollback decision; request agreement before new tables
  or unanticipated storage expansion. Do not claim feasibility based only on JSON.
- Files(5): Core src/legal_core/case_preparation_contracts.py (new),
  src/legal_core/analysis_contracts.py, tests/test_case_preparation_contracts.py (new),
  tests/test_preparation_storage_feasibility.py (new), root docs/adr/0091-pre-report-case-preparation.md (new).
- Verify: `python -m pytest -q services/legal_core/tests/test_case_preparation_contracts.py services/legal_core/tests/test_preparation_storage_feasibility.py`.
- Dependencies: plan approval. Scope M; fail-fast contract/storage checkpoint.
- Evidence: ADR0091, contracts37003b9 and88a2ec8, isolated review accepted both
  actionable nested-validation findings and verified their fixes. Actual existing-
  storage roundtrip/unique-key/RLS tests pass; this does not claim runtime leases
  or authorizing a model response. See PRC2's explicitly incomplete lifecycle.

### PRC2: durable preparation intent and lifecycle — complete, producers disabled

- [x] Bind intent/proposals to server actor/clinic/draft/revision/content; atomically
  deduplicate, fence stale completion and recover pending work without long DB locks.
- [x] Reuse storage only as proven in PRC1; expiry guards include failed/abandoned
  operation records and never replay private content after owner expiry/revocation.
- [x] Add the explicitly planned existing-table lease/receipt/result-version
  expansion and expiry/purge changes with Alembic and runtime-role proofs;
  no producer enablement or deletion of unexpired data/receipts.
- Files(5): Core src/legal_core/intake_preparation.py (new), src/legal_core/models.py,
  src/legal_core/analysis_jobs.py, migrations/versions/<reviewed_preparation_expansion>.py,
  tests/test_preparation_storage.py (new, includes persistence/migration/expiry).
- Verify: `python -m pytest -q services/legal_core/tests/test_preparation_storage.py` with disposable PostgreSQL, migration upgrade/compatibility and expiry proof.
- Dependencies: PRC1 storage decision. Scope M; no new-table assumption.
- Schema checkpoint: migrationd10a6b3e7f91,34 focused runtime-role tests, real empty
  upgrade/downgrade/upgrade and retained-data refusal; concurrent downgrade/write
  regression is RED without locking and GREEN with it. Independent schema review
  plus follow-up found no remaining Required/Critical findings. No producer enabled.
- Runtime checkpoint:d2bd5da+9bf21b2. Separate bounded proposal contracts/tests and
  lifecycle tests implement the planned split; no new storage expansion is added.
  Replay tampering, natural expiry/access withdrawal while waiting, and actual
  retention/completion deadlock are reproduced and fixed. Independent delta review
  repeats46 focused tests on its own fresh PostgreSQL and has no remaining Required.
- Full immutable9bf21b2:2791 passed/0 skipped on fresh PostgreSQL/MinIO; current
  later reader/transport checkpoints require their own full regression below.
- Combined checkpoint:2745 passed/0 skipped with disposable PostgreSQL/MinIO,
  Ruff/140-source mypy/Compose passed. Evidence and remaining boundaries:
  docs/audit/pre-report-preparation-foundation-2026-10-15.md. No deploy/activation.

### PRC3: compatible report-free workflow receipt readers

- [x] Read legacy workflow/report and new preparation receipt without changing
  old report snapshots; new receipts contain no fabricated report identity.
- [x] Gateway/core transport reads both variants and rejects unsupported versions clearly.
- [ ] Establish the complete minimum rollback reader, including the new Telegram
  dialogue consumer, before any report-free workflow is produced (PRC7/10/11).
- [x] Preserve strict draft/fact validation, user-confirmed provenance and current
  server-side tenant/creator/retention rules for new response fields.
- Files(5): Core src/legal_core/api_contracts.py, src/legal_core/case_api.py,
  tests/test_preparation_workflow_compatibility.py (new), Gateway src/telegram_gateway/case_wizard.py,
  tests/test_preparation_workflow_readers.py (new).
- Verify: `python -m pytest -q services/legal_core/tests/test_preparation_workflow_compatibility.py services/gateway/telegram/tests/test_preparation_workflow_readers.py`.
- Dependencies: PRC1–2. Scope M. Checkpoint A: contracts/storage/readers reviewed;
  existing regression green, producer still disabled.
- Evidence:8570ae1+b34c252,d30b8a9. Core GET/replayed POST V2, no artifact duplication,
  expiry/payload mismatch and legacy unchanged; independent reader review found
  no Required/Critical. Shared contract packaging is corrected in1a5c74e/6eb1d2c.

### PRC4: compatible queue result readers — complete, producers disabled

- [x] Job result/status/worker understand intermediate completion independently
  from final ReportResponse; no permanent RUNNING or fake task failure.
- [x] Prevent legacy cached-job replay as V2 and duplicate active work; retain
  lease, idempotency, creator/tenant, retention and notification-ACK semantics.
- Files(5): Core src/legal_core/analysis_jobs.py, src/legal_core/analysis_job_worker.py,
  tests/test_preparation_jobs.py (new), Gateway src/telegram_gateway/analysis_jobs_runtime.py,
  tests/test_preparation_job_readers.py (new).
- Verify: `python -m pytest -q services/legal_core/tests/test_preparation_jobs.py services/gateway/telegram/tests/test_preparation_job_readers.py` with PostgreSQL.
- Dependencies: PRC1–3. Scope M; readers before producers.
- Evidence:d36d602/8798333; independent own-PostgreSQL review81 passed, no skips,
  no Required/Critical; extra READY callback/PDF/notifier and creator/resource/purge
  negatives passed its runtime harness. Shared wire classes are re-exported after
  packaging relocation; no Core API/ORM is included in the Telegram image.

### PRC5: intermediate-aware orchestration transport

- [x] Preserve Core's typed intermediate result through the authenticated
  orchestrator; malformed/out-of-scope results fail closed with bounded errors.
- [x] Existing model/provider/high/deadlines and two-pass legal verification
  stay intact; no legal claims are accepted from understanding output.
- Files(4): Agent src/agent_orchestrator/legal_core_client.py, src/agent_orchestrator/main.py,
  tests/test_preparation_transport.py (new), tests/test_preparation_api.py (new).
- Verify: `python -m pytest -q services/agent_orchestrator/tests/test_preparation_transport.py services/agent_orchestrator/tests/test_preparation_api.py`.
- Dependencies: PRC1,PRC4. Scope M. Checkpoint B: queue/transport readers accept
  both contracts; no producer enabled or legacy output silently reinterpreted.
- Evidence:ab5c884;26 new/43 combined agent tests, bounded auth/lease/protocol/deadline
  failures, exact context fingerprints and wrong-case guards. Independent transport
  and packaging review at6eb1d2c has no Required/Critical; no activation claimed.
- Full6eb1d2c:2904 passed/0 skipped on fresh PostgreSQL/MinIO,199.25s; actual gateway
  Docker build and strengthened exact COPY-set tests pass after the recorded regression.
- [x] PRC5 follow-up split: worker derives version from its stored job, forwards
  only the matching opt-in and checks the current lease before model I/O. Files:
  Core analysis_job_worker.py, tests/test_preparation_jobs.py; no new producer.
- Follow-up:ebb5520 also refreshes an already-loaded ORM row when fencing a lease.
  Independent fresh PostgreSQL42 tests pass, no Required/Critical; its real I/O
  harness proves no runtime DB lock/transaction spans the HTTP request. A removed
  populate_existing mutation is killed by the stale-token regression. The additional
  analysis_jobs.py change and ADR0091 document this critical fencing correction.
- Final code ebb5520:2910 passed/0 skipped, fresh migrated PostgreSQL+MinIO,
  194.05s; Ruff/all-three-service mypy142/Compose and actual gateway image pass.
  No production release or full Telegram dialogue completion is claimed.

### PRC6: persist late clarification without a report/PDF — late writer implemented

- [x] Core validates questions and fingerprints, persists verifier/risk/audit
  evidence and the preparation result, but creates zero CaseReport/PDF artifacts.
- [ ] UNKNOWN/APPROXIMATE, no valid evidence and HIGH/CRITICAL remain honest
  limited/human paths; never invent missing facts or turn an empty batch READY.
- [ ] Bind persisted intermediate data to the actual immutable case snapshot;
  no dummy report or invented verifier-run identity for pre-context stops.
- Files(3): Core src/legal_core/analysis_api.py, src/legal_core/case_preparation.py (new),
  tests/test_preparation_analysis.py (new).
- Verify: `python -m pytest -q services/legal_core/tests/test_preparation_analysis.py` with PostgreSQL/MinIO.
- Dependencies: PRC3–5. Scope M.
- Thin implementation/fix checkpoints:8a86ce9,9b4557d,834a967,94a9751.
  The actual late source uses its persisted verifier run and fact/context hashes;
  NEEDS_FACTS/LIMITED create no report, while READY yields a real downloadable PDF.
  Pre-context UNKNOWN/urgent paths and recovery remain PRC6a/6b below, not closed
  by this writer. Shared endpoint/queue integration corrections were split into
  separate commits with their tests and ADR0091; no oversized single task commit.
- Independent review found and reproduced same-case cache substitution, parallel
  double finalization and a job reader bypass. Fix review:9 actual PostgreSQL
  checks pass. Additional fresh/cached lock-wait access review:20 pass. Author
  combined producer/queue/legacy analysis/access regression:94 pass, Ruff and
  all-three-service mypy143 pass. Full94a9751 after environment recovery:
  2938 passed/0 skipped,171.27s on fresh migrated PostgreSQL/MinIO; Compose passes.
- Recovered the committed branch to a persistent worktree; original dirty tree
  preserved. No new source was lost, no production changes or release claimed.

### PRC6a: honest limitation before legal context exists

- [ ] Missing/unknown event date, attempted UNKNOWN screening and absent evidence
  yield typed preparation limits without calling the model or generating a report.
- [ ] Preserve exact-date, approved-evidence and risk refusal. Known urgent facts
  still route immediately; do not interpret UNKNOWN as NO or invent policy results.
- [ ] Classify only allowlisted factual/evidence blockers, not auth/network/internal
  failures; attempts stay preserved for the form instead of an automatic repeat loop.
- Files(4): Core src/legal_core/analysis_api.py, src/legal_core/case_preparation.py,
  tests/test_preparation_precontext.py (new), tests/test_preparation_urgent_limits.py (new).
- Verify: `python -m pytest -q services/legal_core/tests/test_preparation_precontext.py services/legal_core/tests/test_preparation_urgent_limits.py` with PostgreSQL.
- Dependencies: PRC6. Scope M.

### PRC6b: Core recovery bound to intermediate source

- [ ] Add a strict operation-result origin alongside legacy report-bound origin:
  operation ID/hash, case/fact hash and available context fingerprints, server-validated.
- [ ] Creator/tenant/expiry/latest-source checks and stable confirmation keys
  preserve immutable originals and original material ACLs; do not copy files.
- [ ] Preserve UNKNOWN/APPROXIMATE attempts during prefill; old report callbacks
  and correctionSource still validate without treating a stale report as a new result.
- Files(4): Core src/legal_core/case_clarification.py, src/legal_core/api_contracts.py,
  tests/test_preparation_recovery.py (new), tests/test_preparation_recovery_authz.py (new).
- Verify: `python -m pytest -q services/legal_core/tests/test_preparation_recovery.py services/legal_core/tests/test_preparation_recovery_authz.py` with PostgreSQL/MinIO.
- Dependencies: PRC3,PRC6a. Scope M.

### PRC6c: versioned Telegram clarification intent

- [ ] Encode the intermediate source in a callback within64 bytes; decode and
  validate the preview's exact source identity/hash before accepting a new draft.
- [ ] Repeated/stale/forged clicks, creator mismatch and restart do not select
  another source, clone another case or fall back to a fake/old report.
- Files(5): Gateway src/telegram_gateway/clarification_intent.py,
  src/telegram_gateway/clarification_runtime.py, src/telegram_gateway/case_wizard.py,
  tests/test_preparation_intent.py (new), tests/test_preparation_recovery_runtime.py (new).
- Verify: `python -m pytest -q services/gateway/telegram/tests/test_preparation_intent.py services/gateway/telegram/tests/test_preparation_recovery_runtime.py`.
- Dependencies: PRC6b. Scope M. Checkpoint C: late and pre-context limitations,
  report-free provenance and legacy recovery are all verified before form activation.

### PRC7: shared late-clarification form and report-free initial receipt

- [ ] Versioned initial submission uses the approved preparation receipt rather
  than creating its old intake PDF; files/triage/audit and case limits stay intact.
- [ ] Gateway renders the same factual form for intermediate results, no empty
  result/PDF buttons; final reports and old cases remain usable.
- [ ] Prove double click, stale result, status/result/PDF callbacks and lease/ACK
  recovery; split if actual implementation exceeds five files.
- Files(5): Core src/legal_core/case_api.py, Gateway src/telegram_gateway/bot.py,
  src/telegram_gateway/analysis_jobs_runtime.py, tests/test_preparation_form_runtime.py (new),
  Core tests/test_report_free_workflow.py (new).
- Verify: `python -m pytest -q services/legal_core/tests/test_report_free_workflow.py services/gateway/telegram/tests/test_preparation_form_runtime.py` with PostgreSQL/MinIO.
- Dependencies: PRC3–6c. Scope M; one complete late-clarification path,
  no premature report artifacts, legacy results and early urgency still correct.

### PRC8: constrained Hermes understanding, not legal analysis

- [ ] Interpret only permitted existing V2 fields, each anchored to actual text;
  absent/ambiguous/negated facts stay unknown, no arbitrary questions or URLs.
- [ ] Validate de-identification before the call and model result afterwards;
  no files/model tools/new provider, no suggestions promoted to confirmed facts.
- [ ] Timeouts/protocol failures produce manual continuation, not fabricated facts.
- Files(5): Agent src/agent_orchestrator/intake_understanding.py (new),
  src/agent_orchestrator/main.py, src/agent_orchestrator/contracts.py,
  tests/test_intake_understanding.py (new), tests/test_intake_understanding_api.py (new).
- Verify: `python -m pytest -q services/agent_orchestrator/tests/test_intake_understanding.py services/agent_orchestrator/tests/test_intake_understanding_api.py`.
- Dependencies: PRC1–2,PRC5. Scope M.

### PRC9: protected understanding operation API

- [ ] Existing draft owner initiates/reads a bounded operation; reauthorize at
  completion/acceptance, validate revision/hash and refuse cancelled/stale output.
- [ ] Intent/result survives restart through PRC2 lifecycle; concurrent retries
  neither duplicate operations nor hold DB locks across network waits.
- [ ] Test creator/tenant/subscription negatives and privacy-safe audit codes.
- Files(5): Core src/legal_core/intake_preparation.py, src/legal_core/case_api.py,
  tests/test_intake_preparation_api.py (new), tests/test_intake_preparation_authz.py (new),
  tests/test_intake_preparation_races.py (new).
- Verify: `python -m pytest -q services/legal_core/tests/test_intake_preparation_api.py services/legal_core/tests/test_intake_preparation_authz.py services/legal_core/tests/test_intake_preparation_races.py` with PostgreSQL.
- Dependencies: PRC2–3,PRC8. Scope M. Checkpoint D: report-free receipt and
  protected early understanding run without losing pending/expired operations.

### PRC10: confirmed understanding and one pre-report form

- [ ] Quick entry displays editable understanding; only explicit confirmation
  prefills the durable draft; later model output cannot overwrite user answers.
- [ ] Both entries ask only missing allowlisted fields, bind dates/payment to
  the chosen service episode, and preserve attempted UNKNOWN/APPROXIMATE.
- [ ] Back/cancel/resume/edit work; optional model wait never prevents manual
  continuation or confirmed urgent routing. No jargon or false completion message.
  confirm_case/confirm_v2_summary must not reopen an attempted event/safety answer;
  route to the PRC6a limitation without weakening the legal/risk gates.
- Files(5): Gateway src/telegram_gateway/guided_intake_runtime.py,
  src/telegram_gateway/guided_case_intake_v2.py, src/telegram_gateway/bot.py,
  tests/test_confirmed_understanding_runtime.py (new), tests/test_shared_pre_report_form.py (new).
- Verify: `python -m pytest -q services/gateway/telegram/tests/test_confirmed_understanding_runtime.py services/gateway/telegram/tests/test_shared_pre_report_form.py`.
- Dependencies: PRC7–9. Scope M. Checkpoint E: both user paths and fallback tested;
  no public activation from partially implemented slices.

### PRC11: complete integration and independent review

- [ ] Fresh quick/guided positives and UNKNOWN, course/episode ambiguity, multiple
  services, prompt injection, edit/cancel/restart and stale/double click work end-to-end.
- [ ] Assert zero intermediate CaseReport/PDF objects, immutable originals,
  correct material ACLs/expiry, no repeated questions, unchanged urgent notifications.
- [ ] Full PG/MinIO, quality/security/container checks and independent review
  pass without skipped tests, lowered checks or unapproved model calls.
- Files(4): root tests/test_pre_report_intake_e2e.py (new), Core tests/test_preparation_retention.py (new),
  Gateway tests/test_preparation_notification_recovery.py (new),
  root docs/audit/pre-report-intake-2026-10-15.md (new).
- Verify: `POSTGRES_INTEGRATION=1 MINIO_INTEGRATION=1 python -m pytest -q --tb=short`,
  `python -m ruff check .`, `python -m mypy services/legal_core/src services/gateway/telegram/src services/agent_orchestrator/src`, `docker compose config --quiet`.
- Dependencies: PRC1–10. Scope M; independent branches for review only.

### PRC12: approved release and fresh live acceptance

- [ ] Review immutable head, preserve backup/approval ledgers and minimum rollback
  reader, pass GitHub CI and approved deployment before claiming production readiness.
- [ ] Actual new fictional quick/guided cases exercise understanding confirmation,
  missing-fact forms, verified plans/PDF and truthful UNKNOWN without intermediate PDF.
- [ ] Check fresh job/report IDs (not idempotent cached results), source/date/action
  support, final delivery and healthy services; record failures and remaining limits.
- Files(2): root docs/audit/pre-report-intake-2026-10-15.md, tasks/todo.md.
- Verify: existing CI plus owner-channel fictional live harness, read-only health/
  revision/approval-ledger checks. No real patient data or unsolicited lawyer test alerts.
- Dependencies: PRC11 and separate release approval. Scope S; not full legal certification.

## Factual analysis clarification — contract approved 2026-10-14

- [x] Read actual synthetic V2 context; disprove the nested INTAKE_VERSION hypothesis.
- [x] Repeat researcher read-only and perform bounded in-memory fictional contrast;
  preserve original failed reports and distinguish model SUPPORTED from Core PASSED.
- [x] Independent isolated treatment-query RED proposal; baseline is unchanged.
- [x] Owner approval of SPEC-analysis-factual-clarifications.md on 2026-10-14.
- [x] Owner approval of the focused plan and FC1–FC11 below on 2026-10-14.

Every task starts with RED, ends with focused GREEN/quality checks and an atomic
commit. Common verification environment: project venv with
PYTHONPATH=services/legal_core/src:services/gateway/telegram/src:services/agent_orchestrator/src.
Core files below are relative to services/legal_core; gateway files to
services/gateway/telegram; orchestrator files to services/agent_orchestrator.

### FC1: strict shared question/answer and compatible read contracts

- [x] Define three allowlisted question IDs, service-slot rules, bounded typed
  answers and additive report/submission defaults; preserve strict validation.
- [x] Verify old payloads and record a reader-compatible rollback revision before
  any new field is written; unknown/injected/duplicate keys never become facts.
- Files (5): Core src/legal_core/{analysis_clarifications,contracts,analysis_contracts}.py,
  Core tests/test_analysis_clarification_contracts.py (new), docs/adr/0087-factual-analysis-clarifications.md (new).
- Verify: `python -m pytest services/legal_core/tests/test_analysis_clarification_contracts.py services/legal_core/tests/test_api_contracts.py services/legal_core/tests/test_report_readiness_contract.py -q`.
- Dependencies: approved plan; medium scope. No new persisted data in this task.
- Evidence: independent RED tests-only eee2e8ac; reader RED 2 failed/9 passed;
  focused GREEN 139 passed, Ruff passed, Core mypy passed (98 source files).
  Source reader baseline is 1c75731. Container rollback additionally requires
  commit `fix: package shared factual clarification contracts in gateway` because
  the gateway intentionally copies only the shared Core modules, not all Core.
  No new producer/production write was enabled by FC1.

### FC2: protect draft and fact inputs

- [x] Validate the new partial draft-answer field and JSON fact using the same
  strict schema; retain byte limits, user-confirmed provenance and V1/V2 separation.
- [x] Reject illegal service slots/shapes and prove omitted/UNKNOWN answers are
  not interpreted as known or negative. No schema/backfill unless demonstrated.
- Files (3): Core src/legal_core/api_contracts.py,
  tests/{test_api_contracts,test_analysis_clarification_inputs}.py (latter new).
- Verify: `python -m pytest services/legal_core/tests/test_api_contracts.py services/legal_core/tests/test_analysis_clarification_inputs.py -q`.
- Dependencies: FC1; medium scope.
- Evidence: input RED 5 failed/9 passed; focused GREEN 146 passed; scoped Ruff
  passed and all three services' mypy passed (138 source files). Reader rollback
  baseline recorded: 1c75731fc3b126ef58a0c83da7077f13ced63a2b plus the gateway
  packaging-fix commit recorded in FC1 above. No production write.

### FC3: carry requests across the model boundary

- [x] Researcher may emit at most three IDs/slots, not free question text or
  answers; empty claims still discard recommendations/draft and skip reviewer.
- [x] Preserve question requests in ReasoningResult and the existing Core client
  submission. Old batches with no requests retain their exact safe behaviour.
- Files (5): orchestrator src/agent_orchestrator/{contracts,reasoning,legal_core_client}.py,
  tests/{test_reasoning,test_legal_core_client}.py.
- Verify: `python -m pytest services/agent_orchestrator/tests/test_reasoning.py services/agent_orchestrator/tests/test_legal_core_client.py services/agent_orchestrator/tests/test_projection.py -q`.
- Dependencies: FC1–FC2; medium scope; no provider/model/tools/time-budget change.
- Evidence: independent FC3 implementation a053ebf integrated as a63af1e;
  RED 7 failed/56 passed, GREEN 163 orchestrator tests; Ruff and mypy passed.
  Prompt explicitly limits questions to GUIDED_V2; legacy SERVICE_TYPE is not
  silently converted into an affected-service slot.

### FC4: persist only Core-validated blocked questions

- [x] Revalidate against frozen tenant case facts, unknown answers and valid
  service slots; reject invalid requests. Store accepted IDs/slots in the existing
  immutable canonical report, not model prose or confirmed facts.
- [x] Preserve context hashes/job fences, retained idempotent replay and safe
  BLOCKED semantics; log only bounded IDs/counts, never answer values/model bodies.
- Files (5): Core src/legal_core/{analysis_clarifications,analysis_api,reports}.py,
  tests/{test_analysis_api,test_analysis_clarification_submission}.py (latter new).
- Verify: `POSTGRES_INTEGRATION=1 python -m pytest services/legal_core/tests/test_analysis_api.py services/legal_core/tests/test_analysis_clarification_submission.py -q` with disposable DB configuration.
- Dependencies: FC1–FC3; medium scope.
- Evidence: scope/builder RED 12 failures; real PG route RED accepted invalid slot;
  GREEN 98 focused tests including real PG empty-claim and verified-partial-claim
  batches, invalid slot rejection, foreign-tenant denial, immutable facts/hash,
  one new blocked report, idempotent replay and metadata-only audit. On-call can
  distinguish factual block using reasonCode and clarificationRequestCount/IDs
  correlated with existing reportId/run resource; no answer or service label logged.

### Checkpoint A — contracts and model/Core path

- [x] Strict contracts, empty-claim request transport and real PG persistence pass;
  incompatible rollout is refused and old histories remain readable.
- [x] Independent scoped boundary review; project lint/type/build checks pass.

### FC5: pure question flow and confirmed fact conversion

- [x] Fixed Russian questions, bounded service mapping and UNKNOWN-aware parsing
  share one pure implementation; a changed service set invalidates its answers.
- [x] Include only user-confirmed answers in V2 facts and factual recap; no
  inferred legacy labels, dates, deadlines, defect or responsibility.
- Files (4): gateway src/telegram_gateway/{guided_case_intake_v2,case_wizard}.py,
  tests/{test_guided_case_intake_v2,test_case_wizard}.py.
- Verify: `python -m pytest services/gateway/telegram/tests/test_guided_case_intake_v2.py services/gateway/telegram/tests/test_case_wizard.py -q`.
- Dependencies: FC1–FC2; medium scope.
- Evidence: independent implementation c152789 integrated as 5416d3e, focused
  GREEN 85 tests; Ruff and all-service mypy passed. First RED missing helpers,
  second RED 5 failed plus missing-helper import. Gateway packaging failure found
  by broader regression was reproduced and fixed separately in 18790cd.

### FC6: durable Telegram question interaction

- [x] Actual application composition supports back/cancel, old-button rejection,
  per-service questions and fresh optimistic saves without overwriting other facts.
- [x] Both quick/manual paths, restart/resume and UNKNOWN completion behave
  identically; no unconditional questions for unrelated cases or infinite loop.
- Files (3): gateway src/telegram_gateway/bot.py,
  tests/{test_analysis_clarification_runtime,test_quick_guided_v2}.py (first new).
- Verify: `python -m pytest services/gateway/telegram/tests/test_analysis_clarification_runtime.py services/gateway/telegram/tests/test_quick_guided_v2.py -q`.
- Dependencies: FC4–FC5; medium scope; no unrelated bot refactor.
- Evidence: independent f240492 integrated as 6e604f1; RED 8 failed/10 passed,
  focused gateway regression 666 passed. Cross-slice omitted global-slot bug
  reproduced separately (1 failed) and fixed in 2c36a50; combined pure/runtime
  GREEN 108 passed. Core-normalized optional global slots are not lost silently.

### FC7: protected source-report recovery in Core

- [x] Creator-only preview/new-draft recovery binds the displayed source report,
  its hash and fact snapshot; stale or cross-tenant recovery fails safely.
- [x] Preserve confirmed answers, source case/report hashes and original material
  ownership. UNKNOWN attempts lead to an explicit limit/human route, not a loop.
- Files (3): Core src/legal_core/case_clarification.py,
  tests/{test_clarification_recovery,test_analysis_clarification_recovery}.py (latter new).
- Verify: `POSTGRES_INTEGRATION=1 python -m pytest services/legal_core/tests/test_clarification_recovery.py services/legal_core/tests/test_analysis_clarification_recovery.py -q` with disposable DB configuration.
- Dependencies: FC4; medium scope; no reopening a finalized case.
- Evidence: independent 4dc724d integrated as 64db365; real PG RED 12 failed/
  12 passed, focused real PG GREEN 64 passed. Source report/fact/content hashes,
  latest-version binding, creator/tenant scope and no file copying are tested.

### FC8: gateway recovery handoff and re-confirmation

- [x] Existing API client passes the protected preview/recovery intent; user
  sees the same filled draft, requested questions and explicit new confirmation.
- [x] Lost replies/repeated clicks reuse the same intent; access is rechecked,
  expired content is not restored and files remain in the original case.
- Files (3): gateway src/telegram_gateway/{case_wizard,clarification_runtime}.py,
  tests/test_factual_clarification.py.
- Verify: `python -m pytest services/gateway/telegram/tests/test_factual_clarification.py services/gateway/telegram/tests/test_analysis_clarification_runtime.py -q`.
- Dependencies: FC6–FC7; medium scope.
- Evidence: independent 76ed0ef integrated as ba8a69c, focused GREEN 91; a fresh
  adversarial review then reproduced two drafts after lost reply + newer report
  (independent RED 93fa235, synthetic and real PG). Fixed via immutable displayed-
  report callback and retained-report metadata selector in bf89c2c → 7831b42.
  Repair focused PG 50 passed; repeated independent review 50 passed/0 skipped,
  no remaining Critical/Required findings. Base64 alias mutation killed 2 tests.
  The actual job CTA was absent for new question reports: vertical RED 8d47195;
  the producer fix is included in 7831b42, vertical GREEN 5 passed after it.

### Checkpoint B — complete factual recovery path

- [x] Real composition → Core → durable draft → restart → answers → recap → new
  confirmation passes; old case, report, source objects and role scopes unchanged.
- [x] Urgent routing is not delayed; quick/manual parity and independent review pass.

### FC9: enforce service-date limits without a new legal resolver

- [x] New nested unknown/approximate answers cannot satisfy a declared factual
  dependency. EVENT_DATE never substitutes for a service date.
- [x] Service-dependent conclusions require support at the relevant known date;
  unknown binding or a later-only approved copy stays blocked. Existing case-date
  evidence checks, unknown recursion and policy remain; no guessed prior edition.
- Files (4): Core src/legal_core/{analysis_clarifications,verifier}.py,
  tests/{test_analysis_clarification_temporal,test_verifier}.py (first new).
- Verify: `python -m pytest services/legal_core/tests/test_analysis_clarification_temporal.py services/legal_core/tests/test_verifier.py -q`.
- Dependencies: FC1–FC4; medium scope; must pass before integration/release.
- Evidence: actual temporal RED 6 failed/6 passed; GREEN 27 verifier/temporal
  tests passed; full intermediate real PG/MinIO regression 2386 passed, 0 skipped
  (104.37s) covers FC1–FC7 plus FC9 working tree, before FC8/FC10 integration.
  Ruff and all-service mypy (138 files) passed. FC9 does not claim that undeclared
  model dependencies or semantic correctness can be proven structurally.

### FC10: one clear question list in Telegram and PDF

- [x] Shared CanonicalReport presentation shows specific fixed factual questions
  with service labels; no duplicate menus or unverified advice.
- [x] Retain sources/qualifiers, blocked status and historical PDFs; older reports
  with no request field remain readable. Long/multi-service output stays bounded.
- Files (3): Core src/legal_core/report_presentation.py,
  Core tests/test_report_presentation.py, gateway tests/test_telegram_report_presentation.py.
- Verify: `python -m pytest services/legal_core/tests/test_report_presentation.py services/legal_core/tests/test_reports.py services/gateway/telegram/tests/test_telegram_report_presentation.py -q` plus synthetic PDF inspection.
- Dependencies: FC4–FC9; medium scope.
- Evidence: independent 2826d41 integrated as 5e462f2; actual RED 2 failed/28
  passed, focused GREEN 39; synthetic PDF visually inspected. Ruff and mypy pass.

### FC11: readiness evidence and reviewed release

- [x] Add synthetic composition/integration proofs for both entry paths, recovery,
  valid/invalid model requests, UNKNOWN, multiple services, temporal denial,
  tenant/creator/expiry/idempotency and unchanged HIGH/CRITICAL routing.
- [x] Full PG/MinIO regression, Ruff/mypy, Compose, existing security/container
  gates and independent review pass; no weakened tests or automatic approvals.
- [ ] After separate immutable-head release approval, normal CI deploy and real
  fictional owner-channel Core-persisted positive + clarification/negative smoke.
- Files (3): tests/test_analysis_clarification_e2e.py (new),
  docs/audit/bot-scenarios-2026-10-14.md, tasks/todo.md. Other earlier tests may be
  extended only within their respective focused tasks, not hidden in this task.
- Verify: `POSTGRES_INTEGRATION=1 MINIO_INTEGRATION=1 python -m pytest -q --tb=short`
  with disposable services; `python -m ruff check .`;
  `python -m mypy services/legal_core/src services/gateway/telegram/src services/agent_orchestrator/src`;
  `docker compose config --quiet`; existing CI security/container commands.
- Dependencies: FC1–FC10; medium scope. Live model SUPPORTED alone cannot close it.
- Evidence: independent 749f7f6 → 4afe25f, strengthened actual-CTA RED 8d47195;
  GREEN 5 tests with real Core/orchestrator/worker/gateway HTTP and PTB composition,
  PG, MinIO, immutable originals and cited evidence. Only external model responses
  and Telegram transport are synthetic. Full combined 2472 passed/0 skipped in
  117.60s on production-code 7831b42; Core-only CI collection subsequently failed
  for a sibling harness import, fixed in d17fb63 without skipping a test; standalone
  vertical test with only the standard three source paths is GREEN (5 passed).
  Source reader/container-compatible candidate is 7831b42, not f4eaee8/1c75731.
  Remote main and VPS remain f4eaee8 with 8 healthy containers; no release performed.
  Subsequent approved release: PR95 head9ebfab1 CI38612321085 passed; merged
  fea949f CI38615867992 deployed successfully, all 8 healthy. Final local head:
  2472 passed/0 skipped/117.48s. Both live fictional quick/guided cases persist,
  but jobs FAILED/ANALYSIS_PROVIDER_INVALID; no new positive legal report/PDF.
  Hermes emits failed/incomplete SSE; read-only diagnostics show timeouts and 403.
  Same-profile minimal technical call passes (6.97s), not legal readiness.
  Rendered request/stale budgets are 29s/high; changing them requires a separate
  owner decision. Approval ledger 54/8 unchanged. The live FC11 gate remains open.

Runtime evidence: docs/audit/bot-scenarios-2026-10-14.md. No new production change
or legal readiness claimed. Online curated reference context remains unimplemented;
67 DRAFT evaluation cases are not automatically approved or sent to models.

## Extended user-scenario audit — owner requested 2026-10-14

- [x] Diagnose CI mismatch: missing native PDF tools, not a legal-verifier failure.
  Two RED workflow tests; 24 focused GREEN; restored isolated test containers:
  2030 full tests passed / 0 skipped in 76.44s. Remote CI must confirm the fix.
- [x] Fix actual case-document dispatch and give explicit unsupported-media guidance.
- [x] Bind V1/V2 actions to current prompt/draft; stale controls cannot overwrite facts/material state.
- [x] Reauthorize before cached output or material consumption; reconcile ambiguous uploads
  and merge attachment flags only into the current protected server snapshot.
- [x] Preserve Unicode filenames across HTTP without weakening parser/privacy/tenant guards.
- [x] Serialize report version allocation; enforce reference-source expiry, including historical replay.
- [x] ACK before protected reads; retain the successful update-local ACK fact through error handling.
- [x] Combined final local suite: 2095 passed / 0 skipped in 124.70s with PG/MinIO enabled;
  Ruff, mypy137, Compose overlays, project Bandit gate, strict dependency audit, actual
  Core/gateway offline non-root imports and 32 native container parser tests passed.
- [x] Independent second-cycle review closed both runtime findings and both test-coverage gaps;
  owner selected current review/tests, without an external CLI invocation.
- [ ] Resolve reference-package author/reviewer independence with owner (auth-contract decision pending).
- [x] Integrate independent RED reproductions and verify all changed paths locally.
- [x] Successful GitHub CI on the final head and explicit owner release approval.
  PR94 CI38563480602 and merged-main CI38564977660 successful; owner approved release.
- [x] Deploy through the existing reviewed main workflow; main f4eaee8 healthy,
  approval-ledger hashes unchanged (54 normative / 8 reference events).
- [x] Both live synthetic entry paths created V2 cases; real Hermes calls completed,
  BLOCKED PDFs persisted/downloaded; quick PDF delivered in Telegram.
- [ ] Successful evidence-linked legal answers for both live paths: both tests returned
  EVIDENCE_NOT_VERIFIED with zero claims. Do not mark this gate green or alter verifier
  to pass it. Diagnose evidence selection/researcher abstention before legal-readiness claim.

Each implementation slice has a failing reproduction and a separate commit.
No normative approvals, role grants or model changes. Deployment occurred only after the
subsequent explicit owner release approval, retaining all original approval history.
Evidence and limits: docs/audit/bot-scenarios-2026-10-14.md. Existing incomplete tasks below
are preserved; technical regression success is not legal validation of private references.

## Unified entry and action-plan report — owner authorised 2026-10-13

- [x] Record approved six-section report and preservation boundaries in SPEC-report-action-plan.md.
- [x] Reproduce quick V1 divergence with independent production-composition tests (14 RED failures).
- [x] Create quick V2 partial draft, preserve unknowns/summary, stable retry and existing V1 resume.
- [x] Validate actual response version/data and provide actionable unsupported V1 clarification.
  Focused local: 75 passed; independent boundary/diff review no actionable findings.
- [x] Shared canonical-only PDF/Telegram view; retain legal/source limitations and blocked state.
- [x] Real PG/MinIO, full regression/security/container gates and independent renderer review.
  Combined suite: 2028 passed, no skips; Ruff/mypy (136 source files), strict pip-audit,
  Compose and offline non-root gateway container import passed. CI gates remain required.
- [x] Visually inspect long/READY/HIGH/BLOCKED PDFs; fix long first-page pagination.
  Independent review found and closed truncated legal qualifiers/citations in compact Telegram.
- [x] Reviewed GitHub CI and explicit release approval; PR94 deployed as f4eaee8 on 2026-10-14.
- [ ] Live model + persisted synthetic report/PDF checks for both version-aligned entry paths.

No weakening of evidence/risk/privacy gates or retrospective rewriting of case content.

## Release audit remediation — owner-authorised 2026-10-13

- [x] Reproduce public-country-name false positive; preserve identifier guards.
  RED: 4 failed / 32 passed; GREEN: 36 passed (privacy + two-pass reasoning).
- [x] Strict complete-package DOCX privacy validation for case/reference materials;
  preserve clinic-document parsing; prove rejection before raw object storage.
  42 focused checks; own separate review found and closed a directory-header gap.
  Delegated candidate DOCX review was unavailable; no independent security sign-off claimed.
- [x] Permit confirmed urgent early routing with unknown dates; align UI shortcut
  with v4 10,000 RUB; keep dated legal analysis blocked and test both Core paths.
- [x] Concrete unknown-fact follow-up and confirmed-new-draft recovery without
  changing prior snapshots, reports or tenant/material ownership.
- [x] Reference comparison cannot PASS missing corpus/infrastructure; distinguish
  reviewed expectations, executable bindings and actual model/legal validation.
- [x] Investigate and measure slow approved-evidence retrieval without weakening
  approval/date/checksum guards; retain optimisation only with verified gain.
  Read-only complete approval predicate: 20.702→6.408 seconds, same 54 versions.
- [x] Expire private draft replies while preserving receipts, active resources,
  ownership/ACL, material-first cleanup and closed-workflow replay protection.
- [x] Ordinary Hermes completes the synthetic two-pass smoke within unchanged
  whole-analysis wall; keep model/provider/reasoning/tool/PII guards unchanged.
  Process-local 65/65 stage probe: 48.435s, one SUPPORTED claim; no Core writes.
- [x] Full local PostgreSQL/real MinIO: 1967 passed, no skips; Ruff, mypy135,
  Compose, Bandit, pip-audit, container builds/imports and Code Graph refresh.
- [x] Independent urgent, clarification, runtime/RTF and retention reviews passed.
  Scope evidence: 75 gateway+14 PG; 59 clarification; 166 runtime/RTF; 42 retention.
- [x] GitHub CI on code 86f7335: run38417457458 successful; PR92, no deploy.
- [x] Owner-approved PR92 released as 236fe9b; main CI38427062926 successful.
  Database backup TOC verified, approval ledger unchanged (54 norms/eight references),
  healthy services. Guided draft persistence/submission/idempotency and seven-group
  menu checked live. This is not a successful complete analysis/release-readiness smoke.
- [ ] Complete live analysis: first provider-invalid response, then safe BLOCKED
  SERVICE_TYPE_UNKNOWN. Investigate provider-invalid separately; do not call it fixed.
- [x] Prepare bounded GUIDED_V2 researcher fact-context fix without changing the
  verifier, dependencies, models/provider/reasoning or risk policy. Ten new tests;
  140 focused passed. In-memory candidate on synthetic PR92 case: 50.265 s,
  14 approved fragments, one SUPPORTED/VERIFIED claim; no Core writes/server config change.
- [ ] Independent review, follow-up CI/release approval and live persisted report test.
- [ ] Human-reviewed structured expectations and actual gold evaluation of the
  67 private DRAFT references; no auto approval or private model sharing.

Contract boundaries: ADR 0081. No automatic normative/reference approval or
private example sharing with a model; production release is separately reviewed.
Technical gates are not a declaration of legal/commercial launch readiness.

## Lawyer-supplied current copies — authorized 2026-10-08

- [x] Verify all 58 originals against archive and production hashes.
- [x] Record owner-approved current-copy evidence contract (ADR 0075, increment 4).
- [x] Add immutable basis/limitations migration and independent DB approval guards.
- [x] Prepare 50 normative originals / 54 parts and 17,119 exact text fragments;
  preserve eight separate references and existing seven groups. No real approvals.
- [x] Verify fresh PostgreSQL: 841 passed, one external MinIO smoke skipped;
  full unit: 1504 passed, 174 separately gated integration tests skipped.
- [x] Fix complete-checklist visibility, stale callback denial and PDF limitations.
- [x] Ruff, mypy (128 sources), Compose and Code Graph refresh passed.
- [ ] GitHub checks, reviewed release and production database backup.
- [ ] Production dry-run/commit REVIEW_REQUIRED, seven-group previews and health.
- [ ] Human lawyer confirms normative groups and references; not an agent action.

## Immediate lawyer alerts — authorized 2026-10-07

- [x] Record already-approved all-active-lawyers direction and minimal internal contract.
- [x] Implement independent Telegram delivery, server eligibility recheck and retries.
- [x] Add transactional tenant-bound outbox and fenced delivery migration.
- [x] Verify disposable PostgreSQL, auth/RLS, recovery and full project gates.
  Evidence: 35 focused PostgreSQL tests; 1081 full local tests with 136 optional
  DB skips; 11 gateway notification tests; Ruff, mypy and Compose passed.
  Full Core PostgreSQL run: 565 passed, one separately enabled MinIO smoke skipped.
- [ ] Independent review and GitHub release; production synthetic delivery check.

## Complete package preparation: discovery, 2026-09-27

- [x] Owner confirmed separate normative/reference confirmation within seven groups.
- [x] Inventory all 58 originals; extract all 51 RTF; inspect all seven PDF covers
  and two additional pages with missing text layers. Record limitations and all
  materials in `docs/legal-review/package-preparation-2026-09-27.md`.
- [x] Review increment 3 in `SPEC-grouped-review-materials.md`, including
  additive storage for preparation and separate human reference-review events.
- [x] Owner approved the implementation plan and requested continuation.
- [x] Owner approved tasks P1–P11 for implementation on 2026-09-27.
- [ ] Prepare, test and deploy the complete workflow; do not equate this local
  discovery record with production readiness or human legal approval.

## Complete package preparation: implementation tasks

Status: owner-approved task breakdown; no legal approval/deployment implied.
Paths below are relative to `services/legal_core/` unless stated otherwise.
Every code task follows red → green tests; only synthetic fixtures enter git.

### P1. Validate an immutable preparation input

- [x] Acceptance: strictly typed, bounded original/hash/title/kind/group, source
  evidence, extraction limitations and intended parts; unknown dates stay null.
  Reference inputs cannot claim normative readiness or approval.
- Verify: `.venv/bin/python -m pytest services/legal_core/tests/test_material_preparation.py`.
- Files (2): `src/legal_core/material_preparation.py`, `tests/test_material_preparation.py`.
- Dependencies: approved tasks; size S.
- Evidence: 13 focused tests; full local suite 980 passed / 91 dependency-gated
  skips; focused Ruff and mypy passed (2026-09-27). No deployed behavior yet.

### P2. Persist preparations without altering receipts

- [x] Acceptance: additive model and Alembic migration, immutable revisions with
  material locking and checksum-bound retry; original receipt import unchanged.
  Runtime grants exclude update/delete of preparation records.
- Verify: disposable PostgreSQL migration and focused persistence tests;
  `.venv/bin/python -m pytest services/legal_core/tests/test_material_preparation_persistence.py services/legal_core/tests/test_review_material_persistence.py`.
- Files (5): `src/legal_core/models.py`, new migration,
  `src/legal_core/runtime_db_role.py`, `src/legal_core/material_preparation.py`,
  `tests/test_material_preparation_persistence.py`.
- Dependencies: P1; size M.
- Evidence: 467 Core/PostgreSQL tests passed, one MinIO-only test skipped;
  separate disposable database upgrade/down/upgrade passed; Ruff/mypy passed.

### P3. Import the private preparation package safely

- [x] Acceptance: explicit bounded CLI input, all originals accounted for, repeat
  run creates no duplicates, paths/checksums verified, missing metadata retained;
  no LegalVersion or approval created by this input path.
- Verify: `.venv/bin/python -m pytest services/legal_core/tests/test_preparation_import.py`;
  separate read-only validation of all 58 private originals and diagnostic output
  containing counts/hashes/reasons only, never full document contents.
- Files (3): `src/legal_core/preparation_import.py`, `tests/test_preparation_import.py`,
  `docs/legal-review/package-preparation-2026-09-27.md` (repository-root path).
- Dependencies: P2; size M.
- Evidence: 7 input/atomic-import tests passed; actual 58-file preparation package
  validated locally (50 legal originals / 54 intended parts and 8 references).
  All extracted texts remain PARTIAL until completeness is independently checked.

### P4. Expose preparation cards read-only

- [x] Acceptance: additive editor API returns current title/classification/known
  metadata/missing fields and original download identity. Existing authorization
  enforced; metadata queries do not fetch full text/raw bytes.
- Verify: `.venv/bin/python -m pytest services/legal_core/tests/test_review_material_api.py services/legal_core/tests/test_editor_groups_api.py`.
- Files (5): `src/legal_core/legal_api.py`, `src/legal_core/api_contracts.py`,
  `src/legal_core/editor_groups.py`, `tests/test_review_material_api.py`,
  `tests/test_editor_groups_api.py`.
- Dependencies: P3; size M.
- Evidence: authenticated Core API and seven-group Telegram cards implemented;
  original download and back navigation retained. Focused Core/gateway tests,
  package validation, Ruff and mypy passed on 2026-09-27.

### Checkpoint A: all originals have preparation cards

- [x] Original 58 checksums and receipt retries preserved; cards enumerate actual
  missing fields and extraction limitations; no accidental normative promotion.
- [x] Focused tests, Ruff and mypy pass; review results before next slice.

### P5. Persist separate reference-review events

- [x] Acceptance: separate append-only actor/time/preparation/hash/batch ledger,
  uniqueness and retry constraints; no LegalVersion/source changes; actor resolved
  by existing server-side LEGAL_EDITOR authorization.
- Verify: `.venv/bin/python -m pytest services/legal_core/tests/test_reference_review_persistence.py` on disposable PostgreSQL.
- Files (5): `src/legal_core/models.py`, new migration,
  `src/legal_core/runtime_db_role.py`, `src/legal_core/reference_review.py`,
  `tests/test_reference_review_persistence.py`.
- Dependencies: P2, checkpoint A; size M.

### P6. Confirm an exact reference group through the API

- [x] Acceptance: preview exact ready/blocked/already-reviewed originals; explicit
  attestation and stable idempotency key; stale/concurrent changes never cause
  partial review; clinical PDFs and form remain excluded from legal retrieval.
- Verify: `.venv/bin/python -m pytest services/legal_core/tests/test_reference_review_api.py`;
  include authorization, replay/payload mismatch, concurrency, rollback and retrieval negatives.
- Files (5): `src/legal_core/reference_review.py`, `src/legal_core/api_contracts.py`,
  `src/legal_core/legal_api.py`, `tests/test_reference_review_api.py`,
  `docs/adr/0046-material-preparation-and-reference-review.md` (root-relative).
- Dependencies: P5; size M.

### P7. Present reference confirmation and preparation cards in Telegram

- [x] Acceptance: same seven roots, clear titles and original downloads, back/cancel
  throughout, distinct norm/reference buttons with exact counts; stable retry key
  after timeout; absent publication/cover dates displayed honestly.
- Verify: `.venv/bin/python -m pytest services/gateway/telegram/tests/test_legal_library_runtime.py`;
  synthetic Core-response rendering and callback flow, no real Telegram sends.
- Files (2, root-relative): `services/gateway/telegram/src/telegram_gateway/legal_library_runtime.py`,
  `services/gateway/telegram/tests/test_legal_library_runtime.py`.
- Dependencies: P4, P6; size S.

### Checkpoint B: reference review works without normative promotion

- [x] End-to-end synthetic group review is durable, idempotent and auditable;
  old legal-approval flows still work and reference material is not legal evidence.
- [x] API and gateway regression suites pass; review results before next slice.
- Evidence: disposable PostgreSQL migration/API/concurrency suite passed (3); Telegram suite passed (30); full suite passed (992, 97 dependency-gated skips), Ruff and mypy passed (2026-09-30).

### P8. Prepare complete normative candidates and document parts

- [ ] Acceptance: source URL bound to matching heading, full canonical identity,
  evidenced edition/publication/effective dates, source-linked fragments and
  scoped normalized hashes. Code-part boundaries cannot match repeal notices;
  missing evidence yields an explicit blocker, never guessed metadata.
- Verify: `.venv/bin/python -m pytest services/legal_core/tests/test_normative_preparation.py`;
  separately reconcile all 50 private legal originals with current canonical acts.
- Files (4): `src/legal_core/normative_preparation.py`,
  `tests/test_normative_preparation.py`, `src/legal_core/material_preparation.py`,
  `docs/legal-review/package-preparation-2026-09-27.md` (root-relative).
- Dependencies: P3, checkpoint B; size M.

### P9. Bind every prepared part to a guarded corpus version

- [ ] Acceptance: additive immutable part/version associations, exact-original and
  canonical/text checks; existing corpus loader guards reused; one prepared part
  never hides the other parts; no automatic APPROVED state.
- Verify: `.venv/bin/python -m pytest services/legal_core/tests/test_prepared_material_versions.py`;
  disposable PostgreSQL, failed association rollback and repeated import.
- Files (5): `src/legal_core/models.py`, new migration,
  `src/legal_core/runtime_db_role.py`, `src/legal_core/normative_preparation.py`,
  `tests/test_prepared_material_versions.py`.
- Dependencies: P8; size M.

### P10. Reconcile unified group completeness

- [ ] Acceptance: original downloads retained; canonical alternate copies linked;
  all-part coverage replaces checksum-only suppression for prepared bundles;
  mixed reference/legal progress and blockers determine accurate group completion.
- Verify: `.venv/bin/python -m pytest services/legal_core/tests/test_editor_groups_api.py services/legal_core/tests/test_reference_review_api.py`.
- Files (5): `src/legal_core/editor_groups.py`, `src/legal_core/group_approval.py`,
  `src/legal_core/reference_review.py`, `tests/test_editor_groups_api.py`,
  `tests/test_reference_review_api.py`.
- Dependencies: P9, P7; size M.

### Checkpoint C / P11. Package verification and release

- [ ] Full `.venv/bin/python -m pytest`, Ruff, mypy for Core/gateway, Compose config,
  disposable PostgreSQL/MinIO integration and existing security gates pass.
- [ ] All 58 originals/parts accounted for; exact ready/blocked/reviewed counts
  reported. Unresolved required dates/text coverage prevent a false all-ready result.
- [ ] Human release review → existing GitHub deployment → idempotent preparation
  import → read-only production health/artifact/group/preview verification.
  Never submit a lawyer approval as a smoke check or drop ledgers during rollback.
- Files (up to 3, root-relative): `tasks/todo.md`, package verification report,
  operator instructions in existing `docs/` conventions; no CI redesign.
- Dependencies: P10; size M.

## Audit remediation — owner-approved 2026-09-30

The owner requested implementation of all findings. These tasks extend the existing
corpus plan; they never turn supplied files into approved law and never enable direct
Telegram egress.

### R1. Make Telegram transport fail closed and recoverable

- [ ] Replace syntax-only VPN/gateway health with a token-safe actual proxy-egress probe;
  configure a checked multi-outbound pool and retain a root-only rollback copy.
- [ ] Track successful Bot API connectivity in the gateway; after bounded failure remove
  readiness and stop cleanly for supervisor recovery. No direct fallback.
- Verify: unit timeout/recovery tests, Compose validation, isolated failover probe and
  read-only production `getMe` checks.
- Dependencies: none; size M.

### R2. Authenticate internal analysis routes

- [ ] Require the existing internal key before loading a case analysis context or accepting
  model claims/reviews; update orchestrator client and contract documentation.
- [ ] Add an ADR and tests for missing/wrong key, valid key, tenant actor scope and direct
  result-injection refusal.
- Verify: focused API/worker tests plus disposable PostgreSQL integration.
- Dependencies: R1; size M.

### R3. Bound interactive work and preserve Telegram responsiveness

- [ ] Acknowledge callbacks promptly; use lifecycle-managed short-lived interactive clients.
- [ ] Move document processing and other long paths to bounded durable work, preserving
  per-conversation ordering/idempotency; apply an approved Redis-backed per-user/clinic limit.
- Verify: controlled blocked-Core/upload test with unrelated menu/status updates and limiter
  retry tests; no raw message/document content in logs.
- Dependencies: R2; size L, split before implementation.

### R4. Complete the agreed case intake and recovery paths

- [x] Record the approved v2 ordinary-language intake, deterministic sufficiency and
  isolated anonymised-material contract in `SPEC-guided-case-intake-v2.md` and ADR-0049.
- [x] Add clinic actions already taken, exact-date amendment/reopen and visible Back controls.
- [x] Add a separately reviewed, bounded anonymised case-material flow with migration,
  retention, tenant and parser tests; do not send raw materials to external models.
- Verify: Telegram/API end-to-end synthetic flow, back/retry/date tests and PostgreSQL tests.
- Dependencies: R3; size L, split before implementation.

## De-identified reference-evaluation workspace — owner-approved 2026-09-30

- [x] Record the explicit scope in `SPEC-reference-evaluation-cases.md` and ADR-0050:
  historical examples are de-identified, never legal evidence, retrieval input,
  training data, Hermes context or patient response.
- [x] Add tenant-scoped grants, immutable case versions, reviewer events, RLS,
  storage-first retention, idempotency and PostgreSQL/authz/retention tests.
- [x] Add the private Telegram flow: seven coverage groups, factual recap, one
  bounded TXT/PDF/DOCX attachment, submission, distinct-reviewer decision and
  author revision. Gateway client calls carry only Telegram identity.
- [ ] After the verified production deployment, provision the separately resolved
  memberships for the named owner and lawyer; this is not source-code configuration.

### R5. Finish legal readiness and answer-quality evidence

- [ ] Complete P8–P11 before using the 50 normative preparations for retrieval.
- [ ] Add reviewed taxonomy-to-query coverage and lawyer-reviewed synthetic gold cases for
  every supported group, including abstention and effective-date boundaries.
- Verify: corpus integration, golden evaluation gate and all existing quality/security gates.
- Dependencies: R4, P8–P11; size L, split before implementation.

## Attorney package review, 2026-09-25

- [x] Grouped editor inbox with counts, filters, pagination and preserved originals.
- [x] Retry-safe import accepts safe files with explicitly unresolved provenance.
- [x] Deploy and import all 58 files; verify every stored/downloaded checksum.
  Production import completed on revision 2cd32d6: all 58 originals, retry without
  duplicates and every downloaded checksum verified (2026-09-25).
- [ ] Complete metadata preparation and explicit human approval for all legal copies.
  Receipt, grouping and file download are not normative approval.

## Simplified groups and human batch approval, 2026-09-27

- [x] Seven-group landing page without a separate six-document queue or mixed file list.
- [x] Prepared versions and incoming originals share the same subject groups.
- [x] Explicit ready-subset confirmation with immutable individual audit and safe retry.
- [x] Authorization, stale membership, concurrent confirmations and atomic rollback tests.
- [ ] Full quality/security gates and GitHub deployment.
- [ ] Read-only production verification; do not approve actual laws during smoke tests.

## Task 0.1: Зафиксировать bootstrap-контракт

**Acceptance criteria:**
- [x] Есть capability map, ADR Legal Core и ADR tenant context.
- [x] Есть project rules, spec, dependency-ordered plan и риски.

**Verification:**
- [x] Артефакты согласованы с разделами 0, 4, 19, 25–27 ТЗ.

**Dependencies:** None

**Files:** `AGENTS.md`, `CAPABILITY_MAP.md`, `SPEC-platform-bootstrap.md`, `docs/adr/*`, `tasks/*`

## Task 0.2: Реализовать Legal Core health API

**Acceptance criteria:**
- [x] `GET /health/live` имеет стабильную typed response schema.
- [x] `GET /health/ready` проверяет зависимости и сообщает degraded state.
- [x] Endpoint'ы документируются OpenAPI и не требуют auth.

**Verification:**
- [x] Focused/full pytest проходят.
- [x] Ruff и mypy проходят.
- [x] Runtime HTTP check возвращает ожидаемые коды и payload.

**Dependencies:** Task 0.1

**Estimated scope:** Medium (3–5 files)

## Task 0.3: Добавить локальную инфраструктуру

**Acceptance criteria:**
- [x] Compose описывает PostgreSQL/pgvector, Redis, MinIO и legal-core.
- [x] Сервисы имеют healthchecks, persistent volumes и environment placeholders.
- [x] Секреты не захардкожены; `.env` исключён из Git.

**Verification:**
- [x] `docker compose config --quiet` проходит.
- [x] Контейнеры стартуют и readiness становится healthy.

**Dependencies:** Task 0.2

**Estimated scope:** Medium (3–5 files)

## Checkpoint: Bootstrap slice

- [x] Unit/contract tests, lint и typecheck проходят.
- [x] Compose config и runtime health проверены.
- [x] Git diff проверен на scope и секреты.

## Task 0.4: Подключить Telegram gateway в безопасном режиме

**Acceptance criteria:**
- [x] Токен получается только из environment и не попадает в Git/логи.
- [x] `/start` и `/help` явно сообщают об ограничениях технического режима.
- [x] Приветствие, аватар, описания и inline-меню оформлены в едином стиле.
- [x] Свободный текст не обрабатывается до появления auth/Case Core.
- [x] Gateway запускается без host-портов и от непривилегированного пользователя.

**Verification:**
- [x] Unit tests, Ruff и mypy проходят.
- [x] Telegram API `getMe`, регистрация команд и container health проверены.

**Dependencies:** Task 0.3

## Task 0.5: Добавить CI и закрепить Hermes

**Acceptance criteria:**
- [ ] CI запускает pytest, Ruff, mypy, lock/install и Compose validation.
- [ ] Hermes подключён по проверенному immutable release/commit, а не по `main`.
- [ ] Compatibility smoke test доказывает, что Hermes не обходит Legal Core/MCP boundaries.

**Verification:**
- [ ] CI проходит из чистого checkout.
- [ ] Upstream reference и rationale записаны в ADR.

**Dependencies:** Task 0.4; human review для выбора Hermes pin

## Following: Task 1.1 — identity/tenant data contract

## Task 1.1: Administrator intake and persistence contract

**Acceptance criteria:**
- [x] Intake, missing-facts and report contracts are fixed in a versioned specification.
- [x] Canonical report and legal-corpus lifecycle decisions are recorded in ADRs.
- [x] Alembic creates identity, case, report, audit and legal-corpus tables.
- [x] Tenant identity is resolved by Legal Core and cannot be supplied in a request body.
- [x] Cross-tenant and idempotency contract tests pass.

**Dependencies:** Task 0.4

## Task 1.2: Case Core and blocked intake report

**Acceptance criteria:**
- [x] Case/fact/finalisation endpoints implement the stable error envelope.
- [x] Critical missing facts deterministically produce the next question.
- [x] One canonical JSON creates both the Telegram summary and PDF.
- [x] Legal sections remain explicitly unavailable before evidence gates.

**Dependencies:** Task 1.1

## Task 2.1: First verified legal corpus and retrieval

**Acceptance criteria:**
- [x] Official raw artifacts and metadata are ingested reproducibly with SHA-256.
- [x] Approval is separate from ingestion and audited.
- [x] Retrieval exposes only approved versions applicable on `as_of_date`.
- [x] The 2026-09-01 Decree 736/659 boundary is covered by corpus regression tests.

**Dependencies:** Task 1.1

## Task 2.2: Telegram administrator workflow

**Acceptance criteria:**
- [x] `Создать кейс` is available only to a mapped `CLINIC_ADMIN`.
- [x] The bot collects only the minimum pseudonymous intake data in steps.
- [x] Restart/cancel/retry do not duplicate cases or facts.
- [x] `/whoami` gives the administrator the identifier needed for secure bootstrap.

**Dependencies:** Tasks 1.2 and 2.1

## Task 2.2a: Subscription-gated clinic access

**Acceptance criteria:**
- [x] A `CLINIC_ADMIN` can use protected Legal Core and Telegram intake only with an active,
  time-valid entitlement for that same clinic.
- [x] Suspended, cancelled and expired entitlements produce the stable
  `SUBSCRIPTION_INACTIVE` error and reveal no case data.
- [x] Entitlements and their append-only audit events are tenant-scoped, RLS-protected and
  created by an Alembic migration.
- [x] Internal provisioning cannot change a subscriber into `LEGAL_EDITOR`.
- [x] Payment data, acquirer credentials and payment webhooks are not stored or introduced.
- [x] The configured platform owner can grant `MVP_MANUAL` access by Telegram ID through the bot;
  the server checks ownership, creates an isolated first clinic when required and keeps the action
  idempotent.

**Verification:**
- [x] PostgreSQL API tests cover active, missing, suspended and expired access plus tenant scope.
- [x] Telegram wizard explains inactive access without exposing internal subscription details.
- [x] API and bot tests reject non-owner grants and validate owner grant/replay behaviour.

**Dependencies:** Task 1.1

## Evidence gate

Recommendations, legal risk conclusions and patient-response drafts remain disabled until
approved-only retrieval, applicable-date resolution and claim-to-evidence verification pass.

## Task 2.3: Human legal review of the initial corpus

**Acceptance criteria:**
- [ ] An active platform-side `LEGAL_EDITOR` can inspect the bounded candidate list, immutable
  official artifact, effective dates, checksums and selected fragments in Telegram; clinic roles,
  subscription and platform-owner ID do not substitute this server-side role check.
- [ ] Only a qualified `LEGAL_EDITOR` can make all four explicit attestations and submit a
  checksum-bound, idempotent approval through Legal Core; stale or legacy candidates fail closed.
- [ ] A qualified, platform-side `LEGAL_EDITOR` has reviewed the immutable official artifacts using
  `docs/legal-review/initial-corpus-review.md`; no version is approved by this task automatically.
- [ ] The PP №659 fragment selection covers the intended recommendation scenarios before approval.
- [ ] Every approved version has a checksum-bound, append-only approval attestation.

**Dependencies:** Task 2.1; `SPEC-legal-review-workspace.md`; explicit human legal review

## Task 3.0: Approve the evidence/risk/agent release packet

**Acceptance criteria:**
- [ ] A qualified platform-side `LEGAL_EDITOR` approves legal-base v1 scope and every
  checksum-bound artifact/selection; subscriber and platform owner cannot substitute approval.
- [ ] Product owner approves covered incidents plus monetary-threshold semantics.
- [ ] Legal editor approves `risk-policy.v1` triggers and regression scenarios.
- [ ] Security/product owner approves provider/data processing and an immutable Hermes revision,
  or explicitly keeps the provider disabled.

**Dependencies:** Task 2.3

**Reference:** `SPEC-evidence-risk-agents-updater.md`

## Task 3.1: Versioned deterministic risk and escalation

**Acceptance criteria:**
- [ ] Alembic adds immutable risk-policy, case-risk and escalation records with tenant/RLS scope.
- [ ] Typed facts produce `LOW`, `MEDIUM`, `HIGH`, `CRITICAL` or `UNAVAILABLE` with reason codes,
  policy version and input snapshot hash.
- [ ] Missing policy-required facts and absent policy fail closed; CRITICAL blocks external draft.
- [ ] A policy change makes a new version and cannot rewrite an earlier report.

**Dependencies:** Task 3.0

## Task 3.2: Evidence claims and verifier gate

**Acceptance criteria:**
- [ ] Every legal/action claim has date-applicable approved evidence, source metadata and a
  verifier result.
- [ ] Unsupported, contradictory, inapplicable or incomplete claims block recommendations and
  patient-response drafts deterministically.
- [ ] Contract, tenant-negative, expired-version and no-auto-send tests pass.

**Dependencies:** Tasks 2.3, 3.1

## Task 3.3: Pinned, least-privilege agent integration

**Acceptance criteria:**
- [ ] Hermes is pinned to an approved immutable revision and uses only read-only,
  server-authorised contracts.
- [ ] Provider adapter is disabled unless a provider/data-processing decision is approved.
- [ ] Pseudonymisation, timeout, audit-redaction, authz and bypass-negative tests pass.

**Dependencies:** Tasks 3.0, 3.2

## Task 4.1: Deterministic legal updater and comparison review queue

**Progress note (2026-09-10):**
- [x] Production watcher sends requests to `publication.pravo.gov.ru` only through the existing
  internal VPN proxy; an unset, malformed or non-allowlisted proxy fails closed and cannot divert
  official-law traffic to an arbitrary endpoint.

**Acceptance criteria:**
- [ ] Versioned source allowlist, immutable fetch, SHA-256 idempotency, parse and structural diff
  produce `REVIEW_REQUIRED` candidates only.
- [ ] Automatic promotion is impossible; reviewer attestation and regression are required.
- [ ] Fetch/parser/source-boundary failures are auditable and fail closed.

**Dependencies:** Tasks 2.3, 3.0

## Task 5.1: Controlled free-pilot entitlement

**Acceptance criteria:**
- [x] Product owner approves owner-granted, time-limited `FREE_PILOT` access rather than open
  public self-registration.
- [x] The existing user+clinic entitlement guard applies equally to free pilot access and cannot
  bypass evidence/risk/verifier gates.
- [x] Payment data and new user identity categories remain absent.

**Dependencies:** `SPEC-free-pilot-practice-research.md`; product/security approval

## Task 5.2: Licensed and reviewed practical-scenario regression library

**Progress note (2026-09-04):**
- [x] Public-topic discovery is recorded in
  `docs/legal-review/practical-scenario-discovery-2026-09-04.md` without copying posts,
  treating them as legal evidence, or adding them to the corpus/training set.
- [ ] Convert only licensed, de-identified and independently reviewed candidates into executable
  regression fixtures.

**Acceptance criteria:**
- [ ] Each scenario has documented provenance/rights, de-identification, two-person legal review,
  official-source links, expected actions and expiry review date.
- [ ] Public forum text is not scraped, stored as a corpus, used as production evidence or used
  for training without written permission/licence.
- [ ] P0 scenario failures block corpus/policy promotion; fixtures contain no real patient data.

**Dependencies:** Task 3.0; legal/security approval

## Task 1.3: Durable Telegram intake drafts

**Acceptance criteria:**
- [x] Legal Core stores multiple active, pseudonymous drafts per administrator and clinic with RLS,
  optimistic revision and an Alembic migration.
- [x] Create/list/read/update/archive contracts use server-side actor scope, idempotency and
  tenant-negative tests; list entries contain no free-text patient facts.
- [x] Telegram persists each accepted transition, presents **Мои черновики**, supports switching
  and restarts from the exact next question.
- [x] Leaving to the menu preserves the draft; explicit archive and completed submission remove it
  from active drafts without creating duplicate cases or reports.

**Verification:**
- [x] Unit/contract suite, Ruff, mypy and Compose validation pass.
- [x] Isolated PostgreSQL migration/API suite proves RLS ownership, revision conflict and 30-day
  purge; production deployment completed on 2026-08-31.

**Dependencies:** Tasks 1.1, 2.2a; `SPEC-durable-telegram-drafts.md`

## Task 0.6: Controlled GitHub-to-VPS deployment

**Acceptance criteria:**
- [x] Every pull request and `main` update runs Ruff, mypy, pytest, Compose validation, PostgreSQL/MinIO integration tests, dependency audit and CodeQL.
- [x] A successful `main` build may deploy only through the protected `production` environment and a key-only, forced-command server account.
- [x] Production runtime secrets remain only in a root-owned server file and are absent from Git, workflow logs and GitHub repository variables.
- [x] Deploy validates that the requested commit is reachable from `origin/main`, runs the base Compose profile and proves `/health/ready`.
- [x] Rollback is an explicit production workflow and deployment steps are documented.

**Verification:**
- [x] Workflow files pass static validation and the full local quality bar passes.
- [x] Server Docker/Compose installation and restricted deployment account are verified without enabling the application.
- [ ] A commit after runtime configuration reaches a healthy base-profile deployment; Hermes and maintenance profiles remain disabled.

**Dependencies:** Task 0.5; explicit human provision of Telegram production configuration

## Task 6.1: Six-point bot reliability remediation

**Acceptance and local evidence:**
- [x] Human-approved verified copies reach production retrieval; a real Core/orchestrator/worker
  HTTP integration test produces an evidence-linked LOW report and persisted PDF after approval.
- [x] Pending/date-inapplicable evidence blocks recommendations; software does not approve laws.
- [x] Analysis intent survives app recreation; workers use exclusive expiring leases and reconcile
  committed responses; duplicate callbacks do not create concurrent work for a case.
- [x] Versioned opt-in early triage creates HIGH/CRITICAL cards with unavailable evidence/model;
  v1 behavior stays unchanged; deterministic routing does not authorize legal recommendations.
- [x] Actual PTB dispatcher tests isolate wizard/quick/admin/discussion modes and old buttons.
- [x] Lawyer card, claim, answer, resolution, chronological history and pagination have role,
  tenant, concurrency, retention and immutable-audit regression tests.
- [x] Editor can download full source PDF and complete selected excerpts; list queries avoid
  pulling PDF/full-text columns or running approval preflight on every row.
- [x] Independent security review findings were reproduced, fixed and rechecked by root.
- [x] Clean local PostgreSQL/MinIO suite: 503 passed; all-three-package mypy, Ruff, Compose and
  dependency audit passed. Final file-delivery closeout requires one more integrated run.
- [ ] GitHub CI passes on the final release commit; human review/merge approval recorded.
- [ ] Deploy via existing main workflow and verify production service, migrations and queue.
- [ ] Owner authorizes and activates the new early-triage policy, retaining the 50,000 RUB threshold.
- [ ] Lawyers manually approve applicable legal versions; owner checks private-chat user flow.

**Dependencies:** `SPEC-bot-reliability.md`; ADR0021/0023/0024/0026/0029/0030.

## Task 6.2: Integrated launch audit (2026-09-15)

- [x] Integrate PR #42 without dropping durable analysis, lawyer workspace or reviewed-copy retrieval.
- [x] Restore private Telegram delivery, durable input transitions and complete long reports.
- [x] Persist model abstention, reject unknown/invalid evidence dependencies and uncertain dates.
- [x] Validate fenced job headers before invoking models; preserve committed results on timeout.
- [x] Prevent starvation and concurrent replacement in official-source updates; provide hash-locked PDF recovery.
- [x] Fix Alembic password escaping and provide persistent opt-in full Hermes deployment.
- [x] First integrated GitHub run passes: 500 general tests and 301 Core/PostgreSQL/MinIO tests
  on `ddd0da92a3e5af0eb1c2837945a8488a6c557778`, including the synthetic HTTP end-to-end scenario.
- [x] Final CI passes after the fixed MinIO source-build update and official PDF verification
  (PR run 34994520047; main quality and integration jobs in run 34996415369).
- [ ] Production retry after SSH disconnected during the initial MinIO source build;
  keepalive and smaller build concurrency are being validated. OOM is not confirmed.
- [ ] Deployment host smoke with actual Telegram/Hermes settings and human-approved legal versions.

**Evidence:** `docs/launch-audit-2026-09-15.md`; PR #43.

## Task 6.3: Guided-v2 risk-policy v3 preparation (2026-10-07)

Bounded implementation and follow-up tasks: `tasks/risk-policy-v3.md`; contract:
`SPEC-risk-policy-v3.md`; ADR 0061. This appends work without replacing unfinished
launch tasks. Activation requires human LEGAL_EDITOR review; the preparation
slice does not establish production readiness or usable LOW/MEDIUM guided cases.
