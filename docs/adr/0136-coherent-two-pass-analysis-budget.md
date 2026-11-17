# Bound both reasoning passes and Core work inside one coherent job lease

Status: implementation, 2026-11-17; activate only with the complete application and migration.

The existing case wall was 150 seconds, the worker HTTP/wall limits 160/165,
and the database claim lease 180. Optional lawyer plans also retained an obsolete
110-second cap. A read-only privacy probe spent 111.242 seconds in the researcher
client and 35.601 in the reviewer, 146.861 seconds overall before the complete
Core preparation and verified write. The native profile's configured 90 seconds
does not bound all queue/wrapper overhead. Each actual reasoning client remains
bounded by 115 seconds, so using two native 90-second settings as a worst-case
application budget would be incorrect.

Set one finite case wall to 260 seconds: two existing 115-second client caps plus
30 seconds for Core and transport work. The worker HTTP limit is 270 seconds and
its total request wall 275; new database claims receive 300 seconds. Optional
lawyer work shares the parent's absolute deadline minus the existing one-second
return margin, including lookup, context, both models and verified submission.
No stage receives a fresh overall budget. A slow dependency can still consume
the shared wall, cancel work and produce an honest unavailable result.

This supersedes only the case/worker/claim values in ADR 0116 and the optional
110-second cap described in the 2026-10-27 lawyer-draft ADR. Intake keeps its
65-second stage and 115-second caller limits. Native profiles, models, high
reasoning, retries, legal source approvals, risk policy and permissions stay
unchanged. The existing concurrency limit and durable asynchronous queue remain.
This is an availability correction, not a latency improvement or a new SLA;
requests return immediately when finished, not after a fixed five-minute wait.

The migration replaces only the fixed lease interval in claim_analysis_job,
preserving its signature, SECURITY DEFINER, search_path, OID, owner, ACLs, three
claims, token rotation, SKIP LOCKED and all row-security/authorization contracts.
Existing issued leases and queue rows are untouched. Final source, membership,
retention and actual-current-clock lease checks still decide whether a result
may commit. There is no heartbeat, lease refresh or new automatic retry.

Upgrade the claim function before starting the longer-running application.
For rollback, stop or drain every longer-budget Core worker and Agent first.
Using the new image and its known migration tree, explicitly downgrade to
`b64f1a8c2d53` before starting the old application. Override the normal Core
startup command for this maintenance operation: `services/legal_core/Dockerfile`
runs `alembic upgrade head`, and an old image cannot resolve revision
`c75a2b9d3e64`. Do not run a 260-second producer with new 180-second leases.
Downgrade returns new claims to 180 seconds and leaves existing leases unchanged.
PostgreSQL documents preservation of function ownership and
permissions for [CREATE OR REPLACE FUNCTION](https://www.postgresql.org/docs/16/sql-createfunction.html);
the migration tests verify the actual identity/security metadata and issued TTLs.

Regression evidence must include actual PostgreSQL claim durations, downgrade and
upgrade, token/three-claim limits, current-clock expiry rejection, the complete
timeout ordering, unchanged intake/model caps and a parent's absolute optional
deadline. These structural tests do not prove live provider availability, the
legal quality of all cases, or a response-time guarantee. Full-path live acceptance
and independent review remain release gates.
