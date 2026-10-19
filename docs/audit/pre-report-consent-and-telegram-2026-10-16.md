# Complete factual understanding: consent and Telegram, 16 October 2026

## Scope and release status

Approved PRC10 initial and subsequent-answer implementation, not a production release or a
claim of complete legal readiness. Original dirty workspace is preserved; work is
on fix/pre-report-case-clarification-20261015 in the recovered worktree.
Full application-local composition requires enable_understanding=True and
enable_answer_interpretation=True. The owner explicitly rejected initial-only release;
ordinary main startup remains unchanged/default-false. No SSH, production database,
provider/model calls, external Codex CLI, normative approval or live Telegram
messages were performed. Existing models/high, policy v4/10k and legal evidence
gates are unchanged.

## Implemented boundary

- The direct narrative is saved before optional understanding. Purpose-separated
  UUID5 keys derive from the original actor/chat/message event. Current owned
  draft reads, not a stale create replay, govern recovery. Newly empty drafts
  record observed NOT_ATTACHED metadata; existing attachment markers are retained.
- All nine proposed field groups are shown before consent, including service
  payment/date associations. Proposals remain separate from confirmed draft data.
- Canonical source/proposal digests and append-only audits bind one proposal to
  the exact owned draft revision. Cached JSON substitution is not sufficient.
- Atomic consent locks draft then operation, rechecks current access/retention,
  fills absent fields only and increments revision once. UNKNOWN/NO/approximate
  attempts cannot be silently replaced. Same-request replay returns current later
  edits without reapplying or extending retention. No case/fact/report/job is
  created by this initial factual consent.
- Compact operation+draft callbacks survive local-state loss. Refresh/manual/
  cancel/edit/menu use bounded Core requests; no model wait or polling loop is
  held inside a Telegram handler. Cancellation is durable before local reset.
- The opt-in common summary preserves unknown attempts and uses receipt2 rather
  than an intermediate legal PDF. Its resume asks missing fields; legacy material
  checkpoints preserve their prior behavior. Explicit back/edit remains available.
- Impossible accepted revisions fail closed. A real intervening commit between
  reads refreshes once before a success message.

## Tests and independent review

Code Graph preceded changes and was refreshed afterwards. Current Pydantic,
SQLAlchemy, FastAPI and PTB documentation was retrieved through Context7 before
the respective implementation. Tests preceded behavior changes; existing failing
tests were preserved and implementation corrected instead of lowering the bar.

Focused author selections: 49 proposal contracts/lifecycle; 84 consent/merge/
cancel/lifecycle; 80 actor/contracts/lifecycle/consent/cancel; 258 combined initial
preparation/interpreter/shared-form; 151 legacy/preparation Gateway; 62 material/
form/default isolation after compatibility repair; 33 active UI/navigation/shared
form after final accepted-revision repair. These selections overlap and are NOT
summed into a full-suite count.

Independent atomic review: 21 real PostgreSQL cases, including verified lock waits,
concurrent accept/cancel, current-clock entitlement expiry, creator/tenant denial
and receipt tampering. Removing audit equality and current-clock checks caused
expected failures; restored code passed. Additional public API/DTO review added
31 ownership/safe-stop/coherence checks. Source-bound audit and authorization
defects found by review were reproduced before fixes.

Required review corrections:

1. Discovery initially checked access before its last operation-lock wait; a real
   HTTP/PostgreSQL test returned a proposal after entitlement suspension. Fix
   b61074a repeats access/retention checks after the last wait; the regression passes.
2. Applying missing-fact resume to default legacy material workflows broke 13
   existing upload tests. Fix98c2253 restricts the new behavior to the explicit
   preparation consumer; unchanged legacy tests pass.
3. Bound menu after RAM loss and abandoned edit selectors were corrected in
   b5d4ec5. Lost cancel/accept responses do not announce an unverified outcome.
4. An accepted status ahead of the loaded draft caused a false success message.
   Fixbd86049 validates/refreshes before presentation; negative and legitimate
   intervening-commit tests pass.

Separate actual PTB -> Gateway HTTP client -> Core -> PostgreSQL tests exercise
BOTH quick and guided entries, with no pre-seeded quick draft: save narrative,
reserve, synthetic proposal, refresh without mutation, explicit consent, local
state loss, shared questions, unknown attempts, summary and confirm2. Both produce
one report-free case with the complete submitted fact batch; repeated confirmation
does not create a second case. Zero intermediate reports, PDFs and analysis jobs.
The model proposal and Telegram transport alone are synthetic. These tests do NOT
establish the semantic quality of the configured live model or a final legal plan.
Test-only commit2a40729, source identical to bd86049; authoring agent2 passed/0skips
on separate PostgreSQL55486. Independent replay is recorded separately.

## Subsequent-answer implementation

The ten factual question families have a separate INTERPRET_ANSWER intent,
server-derived current question and answer/source/question digests. One live
answer per owned draft revision; a different reply requires durable cancellation.
Identical retries, including cancelled attempts across midnight, retain the frozen
reference date and never reacquire a model lease. Source text remains bounded,
de-identified, private and expiring; audits contain only IDs/codes/hashes.

Hermes proposes one closed factual value or UNRESOLVED, not a legal conclusion,
permission or executable action. Its existing researcher/profile is reused; no
new model, provider, credentials, reasoning level or concurrency budget. Current
protected context excludes uploaded text/files and routing lists. Service
clarification validation locally reconstructs only the bound typed question,
retaining service-scope and already-attempted-answer guards.

Core revalidates the current lease, source anchors, closed value and immutable
proposal audit before explicit atomic consent. Answer correction changes only
the selected target and canonical dependencies, shown in the preview. UNKNOWN
and approximate dates remain attempts. Foreign/ambiguous or mismatched monetary
amounts fail closed instead of being converted into an invented ruble demand.
Historical receipt replay returns the current draft without rewriting later edits
or extending retention. No case/fact/report/job is created by factual consent.

The full Telegram consumer intercepts free factual text before the manual parser,
returns pending without waiting for a model, and supports refresh, explicit
acceptance, correction/manual continuation, cancellation and recovery. Files and
final confirmation are never interpreted as factual answers. The default legacy
consumer and valid old drafts retain their original resume path.

New verification includes 16 real Core->runner->Agent->protected Core->PostgreSQL
scenarios across all ten families, plus two full actual PTB->Gateway HTTP->Core->
PostgreSQL entry paths with all subsequent answers explicitly accepted. Only
Telegram delivery/model output are synthetic. Both paths create one report-free
case with no intermediate PDF/report/job; unknown dates are not reasked.

Independent reviews added 19 PostgreSQL race/access/retention/audit probes and
five actual PTB recovery/isolation probes. Mutation checks caught removal of
current-clock, output-hash and durable cancellation proof. Full integration review
passed 118 mock/actual PTB/Agent checks and 96 selected PostgreSQL/HTTP checks;
these overlap and are not a full-suite count. Separate exact factory review
approved blob6c7145f1a2a29d39a8cafa89d2dd15a5416729cf before the personal pin update.

Additional integration defects reproduced and corrected: differing answer-hash
algorithms across services, missing incoming-source marker causing first-question
loops, service-clarification validation lacking the protected question context,
nonfactual discovery blocking file/summary navigation, guided resume before its
first narrative and old-draft recovery under full opt-in. Duplicate Core/Agent test
basenames initially blocked full-suite collection; a unique Core test name fixes
collection without changing pytest import mode or dropping a test.

## Final gates

The earlier bd86049 full run was interrupted before a result. It is not clearance.
Final immutable full-suite target86e2c80 has production source identical to013c9f2.
Fresh disposable parent database dental_legal_test_prc_answer_20261016 was migrated
and runtime-role provisioned on task PostgreSQL55487, with separate MinIO59086.
No other process uses this cluster during the full gate. The first013c9f2 attempt
stopped during collection on a duplicate basename, before fixtures ran. The final
run includes the renamed test and independent PTB guards:3965 passed,0 skipped,
0 failures/errors,372.88s. JUnit:
/home/danil/.cache/dental-prc-verify.pLetxJ/prc-answer-full-final-20261016.xml
SHA256:71d6461501ad5f1e712ca04a16421b54554abb7054ac3cd9e0ad83317e521514.
Verified tree:6ca8b0a4637eb7c547ec8f00b885cd745b7b83ca.
The final independent legacy delta adds five test-only negative identity/state
guards; all five pass separately against the identical frozen runtime source.
Neither the original workspace nor the running verification tree was edited.

Ruff and all-three-service mypy171 passed; base and production Compose config
passed with synthetic credentials. Dependency audit of requirements.lock returned
no known vulnerabilities. Final Bandit medium/high scan reports zero findings.
Gitleaks binary download timed out (exit28, empty artifact); nothing was extracted
or executed and no local secret-scan success is claimed. Existing GitHub security
gates, including Gitleaks and CodeQL, are still required before release.

Actual images built from production source013c9f2:

- Gateway dental-answer-gateway-013c9f2-20261016,47382311d8b7.
- Core dental-answer-core-013c9f2-20261016,7bbfb26252a1.
- Agent dental-answer-agent-013c9f2-20261016,12d4fe1e2c04.

All three passed offline non-root/read-only import smoke; Gateway also constructed
the full opt-in application, Core exposed the typed answer routes. No container
had network access during smoke and no production/provider settings were loaded.

## Still required before ordinary activation

The subsequent-answer implementation and full local gate are complete. No initial-only
pilot is authorized. Also required: GitHub CI (including its secret scan), separately authorized
release/activation and fresh fictional live acceptance including source/date/
action verification. Synthetic tests and a running process do not prove legal
correctness or availability of the current production server.
