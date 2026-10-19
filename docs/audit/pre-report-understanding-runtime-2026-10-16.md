# Pre-report understanding runtime checkpoint — 16 October 2026

Final implementation/test snapshot: `b48c11be1fd61a200b78d2f814a0bde2ef9a9038`, tree
`f436cd1553dca57dfd2ff3c9fdfdf525a8459640`. Production service sources are identical
to earlier75b523f/tree3d1e1e05b1f9d036dbfe860924b7b726d268992c; only the clinical
baseline canary and ADR changed afterwards.
Branch: `fix/pre-report-case-clarification-20261015`; original dirty worktree preserved.
This is not a deployment, legal certification or completed Telegram intake flow.

## Implemented boundary

- The existing intake owner initiates/reads/cancels one revision/content-bound
  durable operation. Public DTOs expose only ID/state/unconfirmed suggestions.
- Agent accepts metadata, not arbitrary source. Core releases only the owned,
  current, unexpired, de-identified description for the current private lease.
  Access/entitlement/current-clock retention/lease/safe-stop are checked after locks.
- One existing researcher call returns strict, source-anchored factual proposals.
  Files, IDs, tokens and full draft data are not model inputs; no facts are confirmed.
- One application-scoped two-slot admission budget covers existing analysis and
  intake. Analysis acquires before job claim; optional intake never queues behind
  busy capacity. POST returns without waiting for the model.
- Cancel fences late completion and preserves the saved draft. Failure, busy
  capacity and shutdown yield durable manual continuation, not automatic model retry.
  Structured shutdown drains tasks before HTTP-client/database disposal; cancellation
  before a coroutine's first instruction is covered too.

Models/provider/high, stage65s/total115s, worker HTTP120s/wall125s, risk v4/10k,
approved norms, legal authority, subscription/tenant permissions and personal M1
are unchanged. No new dependency, table or migration was introduced.
Actual Code Graph MCP targeted the correct worktree before analysis and reindexed
each change. Its606 nodes/605 edges do not establish complete Python impact coverage.

## Tests and review

Author RED/GREEN increments include private context21, private API11, Agent context
transport16, Agent API14, admission2, durable cancellation4, runner/lifespan8 and
actor API8 tests. Focused combinations:48 Core,32 context/API,39 transport,96 Agent,
56 privacy/client/API and54 runner/cancel/lifecycle/admission/health tests passed.
These overlap and must not be summed as unique tests. An intermediate cancellation
test wrongly assumed a public status field; it failed and was corrected to inspect
actual stored status in c01b4d3. An invalid test-file selection ran no tests and is
not verification. No gate/test/assertion was disabled or weakened to get green.

Independent reviewer branch/worktree: `review/prc9-private-context-20261016`,
`/home/danil/Рабочий стол/Urist_bot-prc9-review-20261016`, only task PostgreSQL55486.
Initial final boundary review192 passed/0 skips; real row waits, same-clinic creator,
privacy-safe audit and four Agent→Core→PostgreSQL calls use synthetic model stand-ins.
Both draft/operation rows accept FOR UPDATE NOWAIT during the model call.
Removing the post-lock safe-stop guard and inverting the lease-token guard each
caused test failures; mutations were restored. Independent probes ed014a2/093f8d6
are retained on the author branch as fad0d2b/57a612f.

Required R1 was reproduced: malformed JSON/transport errors retained raw response
or credential-bearing requests in exception cause/context. Fix1e01a95 sanitizes
the whole new context method outside except; legacy client behavior is unchanged.
Reviewer's two privacy regressions and author's three marker tests pass afterwards.
Final independent delta review on reviewer commit
`a33c1cf479efa7258a05b3c0dcc8fc593d720c58`: **224 passed/0 skips/90.95s**,
Ruff/mypy149 pass, no remaining Required/Critical. Twelve additional full-stack
probes exercise actual actor POST→runner→Agent→Core→PostgreSQL, model-wait draft
edit/access withdrawal/cancellation, foreign/same-clinic/revoked HTTP callers and
actual lifespan shutdown ordering before engine disposal. The real analysis
worker/claim/queue shares admission with intake: max2 active calls, second job
stays QUEUED/tokenNULL while capacity is occupied, excess intake immediately goes
manual without a task queue. Removing the cancellation transition lets a genuinely
late completion write SUCCEEDED; that independent probe failed and the mutation
was restored. These twelve probes are retained separately in reviewer test-only
commit a33c1cf, not claimed to be part of the author full-suite snapshot below.
Memo: `/home/danil/.cache/dental-prc-verify.pLetxJ/prc9-private-context-review-20261016.md`,
Final memo SHA256 `45b79d2454270af0a52107e7ec00fc1d11ae9830b2b5e3a80d7611e14a21aa41`.

The first full75b523f gate had **1 failed/3288 passed/0 skips/481.99s**, not clearance.
Its sole failure was the old clinical main.py exact pin in the personal M1 guard.
Standalone reproduction:1 failed/7 passed; independent personal suite reproduction:
1 failed/123 passed. The separately reviewed clinic-only startup decision (ADR0091)
advances only that exact pin and strengthens actual route isolation. The first new
canary failed because FastAPI0.141.1 keeps live included routers rather than a flat
APIRoute list; it now traverses them and refuses unknown containers. All other pins
and personal permissions remain unchanged. Corrected personal subset:125 passed.
Context7 resolve-library-id/query-docs retrieved current official FastAPI
[router composition](https://fastapi.tiangolo.com/tutorial/bigger-applications/),
[schema visibility](https://fastapi.tiangolo.com/reference/fastapi/) and
[lifespan tests](https://fastapi.tiangolo.com/advanced/testing-events/) before the
new test edits, as required by the owner's updated AGENTS instructions.

Independent Required R2 found that the first recursive canary lost include prefixes:
a genuinely registered hidden personal path could evade that selected check. Two
permanent direct/nested prefix regressions were RED, then GREEN with inherited
prefix accumulation and fail-closed inclusion metadata. Final personal subset127
passes. The reviewer's original hidden-prefix mutation now fails the selected
composition canary even without the exact-pin test; source is restored afterwards.
The intermediate4885aaa full run was stopped on purpose after867 passes/138.79s,
exit2, when R2 was found. It is not counted as clearance. Production code is unchanged.

Full immutableb48c11b PostgreSQL/MinIO gate: **3292 passed/0 skips/0 failures,
534.29s**,302 existing PTB/library/test-environment warnings, on a fresh migrated parent
`dental_legal_test_prc9_b48c11b_20261016` on task cluster55487, separate from reviewer.
JUnit: `/home/danil/.cache/dental-prc-verify.pLetxJ/prc9-full-b48c11b-20261016.xml`.
Separate concrete baseline-patch review at reviewerf22febb confirms R2 closed,
127 personal tests/0 skips/3.24s after exact mutation restoration and no remaining
Required/Critical. The technical interface decision does not authorize release.
Ruff, all-three-service mypy149 and synthetic Compose configuration pass.
Actual Core image `dental-prc9-core-75b523f-20261016` (`bcfe6e1c8c87`) and Agent image
`dental-prc9-agent-75b523f-20261016` (`5051a1404368`) build and import/route composition
checks pass with network disabled. Gateway sources/COPY set are unchanged here.

## Remaining before user-facing activation

1. Operation-bound, explicit acceptance/editing of suggestions with replay/race tests.
2. Persist the quick-entry description before dispatch; wire Telegram status,
   durable cancellation/menu/resume and confirmation without blocking actor updates.
3. Interpret subsequent free-text answers and use one missing-facts form in both
   entry paths, preserving UNKNOWN/APPROXIMATE and immediate confirmed urgent routing.
4. Complete both end-to-end entry paths, independent final review, full gates and
   separately approved release/live fictional acceptance. No production readiness
   or live semantic model quality is established by synthetic stand-ins.

The next boundary received a separate read-only design review on isolated branch
`review/prc10-acceptance-design-20261016` at b48c11b. The main agent read its full
memo: `/home/danil/.cache/dental-prc-verify.pLetxJ/prc10-acceptance-design-review-20261016.md`.
It specifies proposal/acceptance audit binding, replay before source-staleness
comparison after acceptance, conflict-safe fill-absent merge, read-only operation
discovery and short Telegram refresh/manual/cancel controls. This is a proposed
execution split within approved PRC10, not implemented behavior or another release
approval. No tests, model/provider, database or server work occurred in that review.

No SSH, live provider/model/CLI, production DB migration, normative approval,
deployment or ordinary-producer activation occurred.
