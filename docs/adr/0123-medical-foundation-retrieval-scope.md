# ADR 0123: Scope broad medical foundation queries before candidate limits

Date: 2026-11-05. Status: accepted for the requested audit fixes.

The three fixed foundation queries in `retrieval_plan.py` seek the patient and
paid-service framework. Professional standards and the Tax Code can repeat the same
medical vocabulary and occupy the bounded evidence set without answering that task.
Their presence also increases the text passed to the two legal model stages.

For exactly these three queries, exclude document titles matching the Russian
professional-standard or Tax-Code families before lexical and vector candidate
limits. Apply the same scope to single search, local batches and configured semantic
search. The planner and repository share one fixed query tuple. Targeted qualification,
labour and tax queries retain access to the entire approved corpus, including mixed
batches with a foundation query. This is a retrieval scope, not a legal applicability
decision or a document approval decision. Other unrelated families require their own
evidence and tests rather than extending this scope by assumption.

Use document metadata, not fragment-body words or act-number blacklists. A useful
medical fragment may mention a professional standard or the Tax Code; it remains
eligible. Retain whole fragments, conditions and exceptions, original hashes, URLs,
dates, date basis and extraction limitations. Existing limits, fusion/ranking rules,
approval/source/event guards and effective-date boundaries remain in force.

Materialize lowercased queries and Russian `plainto_tsquery` expressions once per
planned query in the batch statement, alongside the existing query-local materialized
corpus. A foundation-only batch can exclude the two families before constructing its
lexical corpus; a mixed batch keeps them available for its targeted query. There is
no cross-request cache. Current approval and date applicability are checked against
the statement's database snapshot on every invocation.

PostgreSQL documents that a materialized WITH query prevents repeated expensive
function evaluation; explicit Russian text-search configuration preserves the prior
normalization semantics. Sources: [WITH materialization](https://www.postgresql.org/docs/current/queries-with.html),
[text-search configurations](https://www.postgresql.org/docs/current/textsearch-intro.html).

## Verification and measurement

`test_retrieval_precision.py` uses disposable PostgreSQL and synthetic text only:

- Eight approved synthetic documents become five foundation documents; retained
  dataclasses compare equal in full to unrestricted retrieval, including a consumer
  remedy's conditions and exception. Targeted qualification, labour and tax recall
  stays complete, including mixed batches. Synthetic evidence text falls from 1,534
  to 1,015 characters without shortening any retained fragment.
- Six stronger irrelevant candidates exceed a limit-one search's three-candidate
  window. Single lexical, batched lexical and semantic search still return the useful
  document. A semantic-only variant has no lexical match and uses deterministic local
  vectors with all six irrelevant documents closer than the useful document.
- Text-body mentions of the excluded families do not remove a useful document.
  Date-boundary queries and loss of the approving editor's active status return no
  foundation evidence through both lexical and semantic branches.
- A 1,500-fragment synthetic batch with seven queries must preserve every field and
  ranking of individual queries and finish within a coarse local two-second read
  budget. This is a regression guard, not a production latency promise.

The comparative microbenchmark uses PostgreSQL 16.12 in the task's own disposable
container. `_seed_large_version(fragment_count=1500, digest_bytes=600)` builds about
1.9 MB of valid synthetic current-copy text, approved through the real attestation
path inside one rollback-only transaction. Baseline is the batch SQL from `e67637a`;
candidate is this ADR's batch SQL. Both use identical date, seven queries and
limit five. Queries: `вымышленный фрагмент`, `фрагмент проверки`,
`проверка производительности`, `вымышленный`, `фрагмент`, `производительность`,
`отсутствующий`. None is a foundation query, isolating expression materialization
from the intentional family scope change. Full ordered result mappings must compare
equal. Time statement execution plus mapping fetch with `perf_counter`, alternate
baseline/candidate order over six cycles, discard the first cycle for both and
compare the five-sample medians. This local test uses the disposable database owner,
warm caches, no network/model calls and no concurrent task tests.

Measured medians: baseline **184.92 ms**, candidate **155.55 ms** (**15.9%** lower).
Baseline samples in milliseconds: 182.64, 181.27, 184.92, 185.54, 186.29;
candidate: 155.20, 156.25, 160.44, 155.55, 152.62. Full mappings were identical.
Scoped validation: **46 tests passed**, Ruff passed, mypy passed for both changed
source modules. Removing only the vector family predicate made both pre-limit
regressions fail, demonstrating that lexical matches do not mask missing vector scope.

These checks establish bounded local SQL behavior and synthetic retrieval precision.
They do not establish production reasoning latency, legal completeness, semantic
quality of external embeddings, or completion of the audited live case. Those need
an unchanged-model end-to-end run and legal review of the retrieved evidence.
