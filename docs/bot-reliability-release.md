# Bot reliability release and verification

Date: 2026-09-12. Base production revision: `f9e116506eec090986b66a46353dc970288265b9`.
Status: implemented and locally verified; **not deployed**; legal documents are **not auto-approved**.

## Changes and measured evidence

- Lawyers' explicit approval now enables applicable verified copies in library/retrieval/analysis.
  The real HTTP integration test covers approval → retrieval → two-pass model boundary → Core
  verification → immutable report/PDF → durable completion. Only model responses are synthetic.
- Long analysis runs in a PostgreSQL-backed queue, two consumers, 180-second fenced lease and
  bounded execution; committed results are recovered without another model call. Telegram
  delivery resumes from database metadata after restart and rechecks current access.
- A new opt-in policy routes known HIGH/CRITICAL facts before model/evidence availability.
  Absence of evidence continues to prohibit legal recommendations; missing facts do not mean LOW.
- Lawyer workspace exposes full facts, reasons, report/PDF, claim, internal discussion, conclusion,
  resolution and paginated history. Cross-tenant access and expired-case replay are blocked.
- Editor sees source PDF first and may download complete selected excerpts before attestation.
  This export is not represented as a page-exact PDF mapping. Lists no longer load every PDF/text.
- Dialogue transitions do not leak new-case text into a previously opened discussion.
- An intermittent privacy guard bug is fixed: typed service UUIDs are not patient identifiers;
  all untrusted text is still scanned, including UUID-shaped strings entered as text.

Local gates: 503 tests passed on fresh PostgreSQL/MinIO in 11.70 s before the final file-delivery
closeout; Ruff passed; mypy passed for all 79 source files; base/production/Hermes Compose passed;
`pip_audit --strict -r requirements.lock` found no known vulnerabilities. PTB emitted configuration
and deprecation warnings; these are recorded, not suppressed. CI must validate the final commit.

The real PTB dispatcher processed 50 menu callbacks while five synthetic server analyses were
blocked: p95 0.20 ms, target <1 s. This is a control-flow microbenchmark with mocked Telegram/Core
network boundaries, **not** measured production/Internet/LLM latency or a capacity benchmark.

## Human activation and rollout gates

1. Review the PR and its CI results. Merge to main only with human approval; use the existing
   GitHub production workflow, not an untracked server code copy.
2. Confirm a recent recoverable database backup and the actual installed production deploy script
   retains the already configured Hermes/VPN overlays. Never print `app.env` or provider keys.
3. Verify the merged schema head `fc35d7e4a922`, least-privilege runtime grants, Core readiness,
   gateway polling, orchestrator liveness and queue state/age (metadata only).
4. Core now needs the existing `AGENT_ORCHESTRATOR_URL` and `AGENT_INTERNAL_KEY`; Compose passes
   both. Unset URL disables the worker/enqueue; partial invalid credentials fail startup visibly.
5. After explicit owner approval, activate a new risk-policy version with `earlyTriageEnabled`:
   hospitalization or court/regulator involvement → CRITICAL; formal written claim, claimed harm,
   a lawyer involved or demand >=50,000 RUB → HIGH. Preserve the existing policy version as history.
   Do not impersonate a normative reviewer or mark any law APPROVED as part of rollout.
6. Owner checks case creation/back/drafts/analysis/status/PDF; lawyer checks card/claim/discussion/
   resolution; legal editor opens PDF/excerpts and approves only personally checked materials.

Rollback: preserve additive schema and queue data; do not downgrade while jobs are queued/running.
Reverting the old gateway while a new worker processes the same cases requires draining/inhibiting
new jobs first. Policy activation is an explicit separate event and is not undone by code rollback.
Stop rollout for failed health checks, stuck leases or any authorization/data-integrity regression.

## Recommended next improvements (not silently enabled)

1. Corpus coverage dashboard: applicable edition, lawyer, review date, topic coverage, gaps and
   superseded versions. Approval of one document does not mean every question has legal evidence.
2. Personal case list with status, risk, assigned lawyer, pending clarification and result access.
   Add lawyer reminders/escalation SLA only after agreeing delivery rules and recipients.
3. Owner subscription dashboard: who has access, expiry, revocation, issued-by audit and clear
   distinction between user access, lawyer membership and platform legal-editor authority.
4. Record actual callback p50/p95, job queue age, provider timings/errors and file-delivery latency,
   without case text. Establish a production baseline before promising response times or capacity.
5. Reuse HTTP connections in remaining gateway/Core interactions and move remaining clinic-file
   operations out of sequential updates; preserve authorization and draft state when doing so.
6. Plan retrieval indexes/caches from measured queries. Cache only immutable data keyed by the
   approved version and applicable date; never cache away approval invalidation or tenant checks.
7. Monitor MinIO memory headroom and worker concurrency under a controlled load test before
   increasing concurrency or upgrading the VPS. Current read-only host sample: 1,966 MiB total,
   915 MiB available, no swap; this alone does not explain long button waits.
