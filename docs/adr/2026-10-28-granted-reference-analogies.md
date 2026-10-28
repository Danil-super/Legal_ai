# Granted, reviewed scenario analogies in AIF06

Status: owner-authorized AIF06 implementation, initially inactive, 28 October 2026.

The owner explicitly requested that reference cases inform new-case reasoning and
approved AIF06's allowed-reference-example scope. A reviewed historical example is
an analogy candidate, never legal evidence, a confirmed fact of a new patient,
proof that its candidate answer is correct, or a risk-policy decision.

This narrowly extends ADR0050's evaluation-only scenario purpose: only the current
requesting actor's existing CONTRIBUTOR/REVIEWER grant in the same clinic permits
selection of complete, current, independently APPROVED_FOR_EVALUATION scenario
text for the existing model. Workspace access, review rights and approval state
do not change. Ungranted clinic staff receive no examples; no owner impersonation
or role-based bypass is permitted. Raw packages, files and private workbook
exports remain excluded from models, normative retrieval and logs. No reference
case is automatically approved or promoted into a legal version.

The closed pure contract permits at most three complete examples and 12000 total
characters. Exact text hashes, case/version/review identities, coverage group and
evaluation date bind the selection trace. The latter date is explicitly not an
event date or normative edition. Imported cards contain a candidate answer and
warnings; neither becomes authoritative. Exact selected text is screened again
for identifiers; screening is not a privacy certification.

R1 adds contracts and tests only. Later selection must repeat grant, tenant,
current-version, independent-review, live-content and hash checks. Later model
wiring must keep analogy IDs outside legal evidence enums, retain all real fact
dependencies, pass the same approved norms to the independent reviewer, and bind
the selected trace to final source/idempotency/freshness checks. These future
steps are not implemented or enabled by a contract alone. Old readers/wire hashes
and production behavior remain unchanged until separately verified wiring and
the existing release gate.

R2 implements `select_reference_analogies(session, actor=..., facts=...)`, returning
the closed context. It independently checks the resolved actor's active membership,
active user and explicit grant before reading scenario rows, and repeats that
predicate in the source query. The current case/version and an exact independent
APPROVE_FOR_EVALUATION event must match in the same tenant; retired, unapproved,
expired, purged or mismatched content is excluded. Selection reads no attachment
keys/bytes, expected route, reviewer note or raw workbook. A local lexical overlap
of bounded current narrative/service terms ranks at most 200 eligible candidates;
ties use case UUID for deterministic order. Unrelated examples are not padding.
The greedy complete-example budget keeps up to three within 12000 characters,
omitting oversized/unsafe/hash-invalid examples without editing their contents.

This selector is inactive until wired. The caller remains responsible for the
actual case-creator authorization, tenant session, post-model source/grant locks,
exact trace binding and normative claim verification. It does not reauthorize a
Case on its own, certify historical answers or make absent examples an error.
Real disposable PostgreSQL tests exercise current grants, other clinic and same
clinic ungranted users, revocation between actor resolution and selection, exact
approval/version/live-content/hash/privacy checks and complete deterministic budgets.

R3 adds optional `referenceExamples` and paired `referenceExampleTraceSha256` to
AnalysisContextResponse. They are omitted when absent; an empty eligible selection
uses absence, preserving exact old serialized source bytes/hashes. PreparationContext
retains only the optional trace, never historical text. Ordinary submissions add
optional `expectedReferenceExampleTraceSha256`; the private draft source must match
its actual selected context. Field-level Pydantic `exclude_if` preserves omission
even for explicit None. Pair/trace/tamper tests run against the same canonical
serialization used by existing hashes. Producer wiring remains a separate increment.

Source freshness is an internal boolean validation of the Case's server-owned
original creator, not the viewer and not the latest human revision's author. It
does not return historical text or construct a fake actor. Selected case/version
and producer membership/user/entitlement/grant rows are held through final commit;
all predicates and exact selection repeat after lock waits. Retirement, changed
version/content or withdrawn producer access invalidates the trace. An assigned
lawyer without workspace access can still review a valid new-case plan; the
separate historical workspace remains denied. No model/network call belongs
inside the final fenced transaction. Old plans without an analogy trace retain
their prior path, and disabling the producer flag does not erase review access.

The shared analysis-state builder now selects optional examples only when
AI_FIRST_REFERENCE_ANALOGIES_ENABLED=1 and the requesting membership is the actual
Case creator. Both ordinary and separately authorized lawyer contexts use the same
closed optional fields. The private context source retains only their trace.
Ordinary submission requires the exact expected trace and fences its actual
producer/source rows before writes. Legacy submissions omit the field; V2 source
receipts bind it through existing canonical hashes. V1 completion audit adds only
the trace when used. Urgent routing and all normative verifier/risk gates stay
unchanged. No selected examples produces the exact prior absent-field path.

R5 retains the trace in the existing work-product column and the immutable source
context. Additive migration f42d9e6b0a31 enforces their equality with a CHECK; old
absent/null pairs remain valid, no history is rewritten. Human revisions preserve
the same producer/source trace. Public work-product reads validate that source
using the Case creator's current grants and selected provenance; they never return
the example bodies or require a workspace grant from an otherwise authorized
assigned lawyer. Withdrawn producer/source access makes the retained plan stale,
without erasing it or replacing a human version. Private acceptance reuses its
already fenced context and does not repeat retrieval under final source locks.
Downgrade fences writers and refuses retained non-null reference history, including
rows hidden by FORCE RLS from a NOSUPERUSER/NOBYPASSRLS table owner. The temporary
owner-only NO FORCE check is transactional; refusal restores FORCE, and an empty
successful check restores FORCE before removing the constraint. Rollback means
disabling producers on a compatible reader, not erasing or relabelling history.

Independent review reproduced natural example expiry during a later risk-policy
foreign-key wait, after initial source fencing. All three ordinary submission
commit branches now explicitly flush every risk/run/report/receipt write, repeat
the producer/source wall-clock fence, then commit with no intervening write or
network operation. A stale source rolls the entire transaction back. Real PG
regressions observe the actual FK lock wait for legacy report, V2 limitation and
V2 ready branches under the unchanged 5000 RUB/current-v4 scenario.

The same post-flush wall-clock fence covers private draft creation, retained
submission replay and lookup, plus new human APPROVE/REVISE versions and current
human replay. A lawyer-membership FK wait cannot extend the lifetime of a selected
example. The check uses the original Case producer, so assigned lawyers still do
not need a historical-workspace grant. Stale historical read/replay views remain
readable as non-current; no fresh positive acceptance is committed after expiry.
The fence flushes all remaining receipt/audit writes and performs only bounded
local provenance queries before commit, with no repeated normative retrieval.
