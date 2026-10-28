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
