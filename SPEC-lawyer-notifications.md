# Immediate clinic-lawyer alerts

The owner approved on 2026-10-07: notify all active lawyers in a case's clinic
about every HIGH/CRITICAL escalation until it is claimed. The alert contains
only the opaque case identifier and risk level. Files stay in the existing
authorized workspace.

Acceptance:

- Creating an escalation and queuing recipient intents commit or roll back
  together. Repeating assessment persistence does not create duplicate alerts
  for the same escalation/recipient.
- Core determines clinic and recipients; clients cannot specify a destination
  or tenant. Existing active subscription and unambiguous actor access apply.
- Claiming/resolving a case or revoking access stops subsequent eligibility.
  Every send rechecks eligibility; concurrent already-sent alerts remain minimal.
- Restart, temporary Telegram/Core failure and expired worker lease recover from
  the DB. Acknowledged messages do not resend; unacknowledged sends may duplicate.
- Notification polling is independent of Hermes and does not block commands or
  change Legal Core's risk/evidence decision.

Internal API contract (existing X-Legal-Editor-Gateway-Key, minimum 32 chars):

- POST /v1/internal/escalation-notifications/claims: no client destination;
  returns `{items: [{notificationId, leaseToken, caseId, escalationId,
  riskLevel: HIGH|CRITICAL, telegramUserId}]}`, maximum 20 items.
- POST /v1/internal/escalation-notifications/{notificationId}/delivery-check:
  `{leaseToken}` → `{eligible: boolean}`. Checks current lease and eligibility.
- POST /v1/internal/escalation-notifications/{notificationId}/delivery-results:
  `{leaseToken, outcome: DELIVERED|RETRY|UNDELIVERABLE, retryAfterSeconds?: 0..86400}`
  → 204, or 409 for an expired/superseded lease. Extra request fields rejected.

Schemas are strict Pydantic ContractModel classes in
`services/legal_core/src/legal_core/escalation_notifications.py`.
Tests exercise auth, lease fencing, recipients, tenant RLS/composite foreign keys,
retry/restart, revoked/claimed access, payload minimization and gateway failures.
