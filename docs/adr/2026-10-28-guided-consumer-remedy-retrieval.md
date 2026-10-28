# Guided monetary requests include generic consumer-remedy candidates

A live synthetic case described a failed filling and a request for10,000RUB.
The approved corpus contained consumer legislation, but the guided-v2 planner
used only three broad medical phrases. A refund query was previously added only
after the clinic had already offered a refund. All base phrases also required a
medical lexeme in local full-text search: PostgreSQL `plainto_tsquery` combines
surviving words with AND. Generic consumer provisions could not match them.
Source: https://www.postgresql.org/docs/current/textsearch-controls.html.

For guided-v2, a validated factual-safety screening with moneyRequested=YES and
the typed TREATMENT or SERVICE area now adds fixed generic consumer/remedy search
phrases, including the existing rework candidates. No private narrative/service
label becomes a query. The numeric amount and10,000RUB risk boundary do not select
different laws; confirmed requests below and above that threshold get the same
candidate coverage. NO, UNKNOWN, missing/malformed screening, a legacy demand,
or an unclassified narrative alone cannot trigger the new expansion. Existing
queries selected by actual clinic actions remain compatible.

This is evidence discovery, not a finding of defect, entitlement to reimbursement,
an applicable deadline or a preferred remedy. The model and semantic reviewer
must still establish applicability and support; Core still checks current facts,
APPROVED/date-applicable source versions, claim dependencies and evidence traces.
No source, version, approval, risk rule, model, index or query/evidence limit changes.
Queries are deduplicated and round-robin evidence budgeting is unchanged.

Regression tests use a real approved synthetic PostgreSQL corpus with generic
consumer prose lacking any medical lexeme and five noisy medical fragments.
The old plan misses the consumer fragment; the expanded plan retrieves it while
pending/expired versions remain excluded and historical date resolution remains
intact. Tests also preserve v4 MEDIUM/HIGH classification at5,000/10,000RUB and
reject inferred monetary signals. Current broad-query noise is not removed by
this increment; no speed or universal legal-quality improvement is asserted.
