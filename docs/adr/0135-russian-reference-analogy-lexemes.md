# Match Russian word forms when selecting reviewed reference analogies

Status: implemented locally, 2026-11-17; independent review and live acceptance pending.

Exact word-set overlap returned no example for a new case saying «Пломба выпала»
when the approved example said «выпадение пломбы». It also missed «Кариес» versus
«лечение кариеса» and corresponding photograph word forms. These failures were
reproduced with current grants, approved synthetic examples and actual PostgreSQL.

Keep the existing local overlap ranking, using PostgreSQL Russian lexemes on both
sides. One additional bounded SELECT normalizes the already bounded input terms
and existing common-word list. The single candidate SELECT returns each scenario's
lexemes alongside its existing fields; remove that derived field before source
contract validation. Ranking and common-word exclusion use the normalized sets.
There is no per-example SQL request, model, dependency, embedding or file read.

All existing eligibility and privacy checks remain: active creator/user/tenant
membership and subscription, explicit CONTRIBUTOR/REVIEWER workspace grant,
current approved version and valid approval event, live complete source text and
exact source hash. Selection still examines at most 200 eligible candidates,
returns at most three complete examples within 12,000 characters, and resolves
ties by reference-case UUID. Examples remain analogies with short supplied
answers, not normative evidence or new-case facts; both model passes retain the
same selected context and the existing final trace/source fences.

This does not grant ordinary clinic employees access to private examples, enable
the producer flag, approve references, read their full-answer attachments or
fine-tune model weights. A changed selection can make an old plan's context trace
stale; historical source content and human decisions remain unchanged.

Tests reproduce missing inflected forms and an incorrect relevance order before
the fix, then require the complete reviewed text and more relevant example to be
returned. Unrelated examples, inflected common words and Russian stopwords cannot
pad the selection. Existing PostgreSQL grant, retirement, actual-lock-wait,
source-integrity and worker-to-human regressions remain required. These technical
tests do not certify legal reasoning quality or participation of every live record.

Reference: [PostgreSQL 16 text-search functions](https://www.postgresql.org/docs/16/functions-textsearch.html).
