# ADR 0064: Pause automation while preserving lawyer work

Date: 2026-10-07. Status: implemented; production activation pending release checks.

## Context

The direct v1 → v3 transition retires v1 irreversibly. An older binary cannot
interpret v3 safely. SPEC-risk-policy-v3.md therefore requires a compatible
operational stop that retains drafts, reports and urgent lawyer escalations.
DEPLOY_ANALYSIS_ENABLED alone removes the model stack but does not fence trusted
submissions to Legal Core or expose an explicit retry state.

## Decision

Add LEGAL_ANALYSIS_SAFE_STOP to Legal Core configuration. An absent setting or
literal 0 preserves operation. Every other explicitly supplied value pauses
automated analysis, including an empty or mistyped setting. The Compose mapping
preserves empty values rather than interpreting them as 0.

After ordinary actor/tenant checks, enqueue, context and new submission paths
return HTTP 503 / ANALYSIS_SAFE_STOP. The submission path still replays already
committed idempotent results. The worker leaves queued work untouched while
stopped; a job claimed immediately before the stop cannot initiate another model
request. If it has no committed result, it ends in an explicit retriable failure.
No new analysis report, LOW clearance or patient draft is generated in stop mode.

The switch does not approve, retire or edit a risk policy. Existing v3-compatible
case confirmations continue deterministic urgent triage. Draft persistence,
existing job status/result, retained reports and authorised lawyer queue,
claim, discussion and resolution routes remain available. Existing tenant,
subscription and retention checks continue to apply. Manual contact with the
clinic lawyer is offered for ordinary cases; stop mode does not manufacture an
escalation risk level.

Owner diagnostics reports SAFE_STOP without probing the provider. Telegram
explains that the case is saved and offers retry after resumption or lawyer
review. See ops/deploy/RUN_ANALYSIS.md for the operator sequence and its runtime
verification. Returning to 0 is an operator action after corrected release checks,
not a policy rollback; any replacement for approved v3 requires a separately
reviewed monotonic policy version.

## Verification

Tests reproduce the missing stop before implementation. Unit tests fence worker
network calls and context fact reads, fail closed on invalid settings and validate
diagnostic/user messages. Disposable PostgreSQL tests fence enqueue/context/new
submissions, preserve queued work and drafts, retain tenant denial, and exercise
lawyer claim/discussion/resolve. Both confirmation paths run with stop enabled
and still record a single urgent escalation without a corpus or a model call.

No database schema change is required. Tests contain only authored fictitious
facts and internal synthetic credentials.
