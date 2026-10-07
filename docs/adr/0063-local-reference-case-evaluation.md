# ADR 0063: Local evaluation of reviewed reference cases

Date: 2026-10-07. Status: accepted for local operator tooling.

## Decision

Extend the existing de-identified reference-evaluation contract with a local XLSX
readiness audit and CanonicalReport comparator. They neither import workbook rows
into production nor invoke a model. Workbook answer text, `ready_for_training`,
assistant-written `review_status` and workbook `version_date` do not authorize
evaluation or establish an analysis date.

A private review receipt binds the immutable workbook and row hashes to an
independently approved reference-workspace version, reviewed scenario hash,
privacy review, analysis date, coverage group and expected safe route. A lawyer
provides expected risk, policy version and required evidence fragment IDs for
internal drafts. Report comparison additionally requires the operator-reviewed
case UUID and fact snapshot hash; unrelated or changed report content is blocked.
Receipts are local operator assertions copied from the granted review workspace,
not signed approval credentials and not a replacement for server authorization.

Comparisons distinguish BLOCKED (review/binding prerequisite missing), UNAVAILABLE
(analysis not run or blocked), FAIL (observed metadata disagrees), and PASS
(reviewed structural expectations matched). PASS never means that answer prose
is legally correct; a lawyer must separately review actual conclusions and
instructions. An early-triage escalation without a CanonicalReport risk/policy
snapshot cannot prove complete report correctness and remains unavailable here.

## Privacy and operation

Only the `Для бота` sheet is read; source bytes and expanded archive size, rows,
cells and text length are bounded. Formulas, ambiguous IDs and XML entities are
refused. Narrative values stay in local memory and are neither printed nor
included in reports. Direct-identifier scanning is an additional rejection
check, never a substitute for privacy review.

Reports contain only case identifiers, hashes, UUID/version metadata and fixed
reason codes. JSON inputs require an owned regular 0600 file. Reports require an
absolute path through a non-symlink, owned private parent. The writer pins that
directory by descriptor, completes and fsyncs an exclusive random 0600 temporary
file, then publishes it with a same-directory no-replace hard link and fsyncs
the directory. Existing output files and symlinks are never overwritten. Failed
writes or syncs remove the writer's own temporary/published inode so the final
path can be retried; a process crash can leave a private temporary file. CLI
errors omit input values. This output-integrity change leaves PASS structural
and `legalCorrectness=REQUIRES_LAWYER_OUTPUT_REVIEW`.

No tables, migrations, API/MCP endpoints, grants, retrieval behavior, prompts,
risk policies, legal approvals or production features change.
