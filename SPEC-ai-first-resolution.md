# Spec: AI-first case resolution

## Status, objective and assumptions

Behavioral contract approved by the owner on26October2026 ("работай, ты ведь учел
замечания и рекомендации юриста"). Module `ai-first-resolution` in
CAPABILITY_MAP-ai-first.md. Based on production main2eec73b and the owner's
confirmed direction: the clinic describes the situation; AI does the analysis;
the lawyer checks/corrects a prepared work product rather than writing it first.
No implementation, new schema or production enablement is claimed by this spec.

Approved behavioral boundaries:

- One narrative-first creation entry and one final factual-summary consent remain.
- Reuse the entire retained narrative, staged/confirmed answers and later corrections,
  not just the latest sentence. Unknown facts remain unknown; hypotheses are labelled.
- Legal Core owns evidence, effective dates, risk and permissions. The existing model,
  provider and high profile remain unchanged. This is not a second legal-truth model.
- Current risk policy, including the10,000RUB threshold, remains unchanged in this
  module. Revising written-request/health/representative triggers belongs to the
  separately reviewed `escalation-policy` module.

## User-visible result

Describe → interpret retained facts → ask only material missing questions →
confirm one concise summary → provide a grounded internal plan or lawyer-review draft.

The plan contains: what happened/what the patient wants; recommended next steps in
order; relevant options and conditions for choosing them; applicable approved norms
with exact article/point references; remaining uncertainty. Reuse the approved
SPEC-report-action-plan.md presentation instead of inventing a second report menu.
No technical enums, Hermes messages, source hashes or mandatory manual drafting.

LOW/MEDIUM: provide the internal clinic plan when its factual/evidence gates pass.
HIGH/CRITICAL: notify/route immediately under existing policy, independently of
AI availability. In parallel prepare a clearly marked AI work product for authorized
lawyer review. It is not an approved legal answer and does not resolve the escalation.
The lawyer can review, correct and approve the work product, or ask a targeted question;
retain AI/original and human-approved versions with audit and existing access limits.
Do not enable automatic patient delivery or unlock blocked patient-response drafts.

When evidence or facts cannot support a legal conclusion, give only supportable
conditional options/general organizational steps, identify what prevents the specific
conclusion and offer the useful next action. Do not replace missing evidence with
model memory, a reassuring label, or an invented deadline/refund obligation.

## Clarification contract

- No obligatory traversal of seven questionnaire steps or every possible field.
- A question needs an internal reason: which recommendation, legal applicability
  or safety decision its answer can change. Reasons are auditable, not chat clutter.
- Do not ask for an unambiguous value already present anywhere in current retained
  case input. Extract first; preserve provenance and the existing summary consent.
- Ask about an actual contradiction/ambiguity precisely, never restart the whole form.
  Distinguish service date, complaint date and receipt date. Preserve relative-date
  reference. Bind each service-specific answer to the correct service.
- Prefer one small batch of up to3necessary questions. Further questions require
  material remaining uncertainty; a fixed question-count cap never fabricates safety.
- An explicit “не знаю” is retained. Do not repeat it without a changed factual
  context or explaining the newly material reason. Offer a limited plan when appropriate.
- Optional clinic files are not a mandatory gate when supplied facts are sufficient.
  Preserve existing upload/privacy boundaries; do not newly send files to the model.
- Interpret a free reply against the pending questions and all retained context.
  Reuse grounded numeric/date handling; no extra model call just to parse10000RUB.
- In multi-select steps keep one card until «Продолжить →». Retain the shipped
  persistence, stale-card, idempotency, restart and transient-cleanup protections.

## Core/Agent boundary and storage constraints

Current early HIGH/CRITICAL preparation can return HUMAN_REVIEW before a report.
Introduce an explicit internal lawyer-draft path; do not bypass that boundary by
setting analysisAllowed=true, inventing report IDs or relabelling a draft VERIFIED.
Each legal assertion still needs retrieved approved, date-applicable evidence.
Reference cases may inform analogous reasoning, not replace legislation or supply
unconfirmed facts about this patient. No medical diagnosis/treatment prescriptions.

Questions, drafts, corrections and final results must bind to current clinic/actor,
case/draft identity, fact revision and source/evidence version. Reauthorize after
network/lock waits. Deduplicate retries; discard stale completion after edit, expiry,
revocation or navigation to another case. No raw patient material in fixtures/logs.

Before implementation, review the exact typed contract and whether existing
storage can retain separate work-product versions safely. Any new table requires
an approved Alembic migration; this spec does not silently authorize one.
Existing canonical reports, PDFs, legal approvals and old drafts are not overwritten.

## Proposed speed acceptance targets

Targets for review, not measured guarantees: acknowledgement within2seconds without
waiting for AI; first useful clarification/result within45seconds with a healthy
existing provider; plan within90seconds after the final necessary reply. Measure
end-to-end timings on repeated synthetic cases and report provider failures separately.
Do not lower reasoning effort, suppress evidence verification or add uncontrolled
parallel model calls to meet a timing target. Urgent notification cannot await a model.

## Source layout, stack and style

Existing Python3.13 CI, PostgreSQL/Alembic, Pydantic contracts, Hermes orchestration,
python-telegram-bot, ReportLab and pytest; no new dependency/provider proposed.

- Core: services/legal_core/src/legal_core/{analysis_api,case_preparation,
  case_preparation_contracts,reports,report_presentation}.py and adjacent contracts.
- Agent: services/agent_orchestrator/src/agent_orchestrator/, existing analysis and
  intake orchestration, not the deterministic corpus fetch/versioning layer.
- Gateway: services/gateway/telegram/src/telegram_gateway/, existing intake,
  escalation workspace and report delivery. Tests live in each service's tests/.
- ADRs: docs/adr/. Runtime contract changes require tests and an ADR together.

Follow existing typed, fail-closed Python style, for example the existing
`def risk_missing_facts(assessment: RiskAssessment) -> list[MissingFact]:` boundary.
User-facing example: «Рекомендуемый вариант: … Основание: … Если подтвердится …».
“Риск высокий” is not evidence that AI analysis is complete or a medical fact is true.

## Verification and commands

TDD regressions must cover: complete description→no repeated questions→useful plan;
retained dates/amount/actions not reasked; only missing material facts requested;
unknown/contradictory facts; two services with different answers; irrelevant optional
fields; reference-case applicability; citations tied to approved effective versions;
HIGH/CRITICAL immediate routing plus populated lawyer draft; authorized human
correction/approval; missing evidence; provider failure, retries, restart and stale reply;
revoked/foreign-clinic access. Synthetic data only.

Evaluate practical quality with a lawyer on representative scenarios, including a
routine5000RUB request, a written request and an urgent health/court scenario. Score
whether the lawyer can evaluate a prepared solution rather than write one from scratch.
Runtime tests do not establish legal correctness of every real case.

Commands from the worktree after bootstrap, with its own source PYTHONPATH:

```bash
python -m pytest
python -m ruff check .
python -m mypy services/legal_core/src services/gateway/telegram/src services/agent_orchestrator/src
docker compose config --quiet
```

Use existing six-shard disposable PostgreSQL/MinIO CI commands and real PTB tests;
no hidden skips as full integration success. Check live existing-profile synthetic
behavior and user-visible output, not just mocked model responses. Independent
review, successful CI and separate release approval remain production gates.

## Boundaries and approval

Always: source-grounded claims, meaningful questions, preserved unknowns/history,
current tenant/actor authorization, explicit draft status and immediate urgent routing.
Ask first: exact new API/storage contract, migrations, personal-data categories,
provider/model/profile changes, changed risk policy or expanded lawyer permissions.
Never: blanket normative approval, guessed facts/law, suppressed tests, automatic
patient sending, or interpreting consent to this direction as a production release.

The owner approved proceeding with this behavioral/safety contract; timing figures
remain engineering targets, not measured guarantees. The detailed plan/tasks are
now recorded in tasks/plan.md and tasks/todo.md for review. Exact new API/storage
contracts and any necessary migration remain separate implementation checkpoints.
