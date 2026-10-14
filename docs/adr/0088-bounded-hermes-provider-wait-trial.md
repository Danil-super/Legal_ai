# ADR 0088: bounded Hermes provider-wait trial

Date: 2026-10-14. Status: owner approved trial, not proof of legal readiness.

## Context

PR95 live fictional quick/guided cases were saved but analysis failed. Hermes
emitted failed/incomplete SSE. Diagnostics show timeouts and intermittent 403;
one complete-context call failed at 29.39 s, while a small technical call through
the same gpt-6.1-sol/high profile passed at 6.97 s. The actual rendered custom
request/stale settings were 29 s. Timing correlation is not proof of sole cause.
The owner approved trying 55 s and continuing diagnosis if it fails.

## Decision

Set only providers.custom.request_timeout_seconds and stale_timeout_seconds to
55 s. This separately approved trial overrides FC's original no-budget-change
scope only for these two settings. Keep model/provider/keys, high reasoning,
api_max_retries=1, tool-free profiles and completion guard unchanged. Preserve
stage65 / shared analysis115 / worker125 / lease180 s fences. Two nominal waits
fit the shared wall, but overhead/retries can still exhaust it; these are not
guaranteed response times. No legal approval, risk policy or retrieval changes.

Pinned upstream supports both provider-wide fields:
[configuration normalizer](https://github.com/NousResearch/hermes-agent/blob/5fc308a70719a83cccdbba4c0e39c23f5a8239d5/hermes_cli/config.py#L1435),
[request/stale resolution](https://github.com/NousResearch/hermes-agent/blob/5fc308a70719a83cccdbba4c0e39c23f5a8239d5/run_agent.py#L1423).
The same resolver locations were inspected in the installed image.

## Operation and recovery

After reviewed CI/release, recreate only the two Hermes services with the existing
analysis Compose overlays, no dependencies, no published ports and no volume
deletion. This runs the existing renderer/tool-free preflight/completion guard.
Verify both rendered profiles show 55/55 and high before live acceptance; a
mounted template change alone does not prove the running profile changed.
Core, gateway, queue, DB, approvals and original cases remain intact.

If the trial must be reversed, restore only these two profile values to 29 through
a reviewed compatible revision and recreate the two Hermes services. Do not
downgrade the application to an incompatible pre-FC reader or delete histories.
Changing models/providers, weakening completion/evidence gates or automatically
approving material is not authorized by trying a longer provider wait.

## Verification and limits

RED: the parsed rendered-profile test expected 55 and received 29. GREEN requires
the two values, unchanged high/retries/tool/memory constraints and outer fences.
Full existing regression and independent scope review remain required. Real
Core-persisted positive plus truthful clarification/negative results are the
acceptance gate; HTTP200, a short transport check or healthy containers are not.
If 403 or incomplete responses remain, diagnose them rather than raising budgets
again or presenting an unverified answer as success.
