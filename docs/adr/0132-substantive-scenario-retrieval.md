# Select substantive evidence for privacy, refunds and payment disputes

Status: implemented locally, 2026-11-17; live corpus and legal-quality validation pending.

Guided privacy cases previously searched only medical secrecy and personal data.
They did not independently select image protection, moral harm or responsibility
for workers/third parties. A confirmed treatment/service monetary request selected
remedies, but did not separately select warranty or defect-origin provisions.
Finance alone had no targeted payment or additional-service search.

Extend the existing fixed query planner with those topics. FINANCE selects
additional services and payment even when a monetary demand is unknown. A valid
confirmed monetary request in FINANCE can select the same consumer-remedy queries
as TREATMENT/SERVICE. This selects possible evidence; it establishes neither a
defect, consent violation, compensable harm nor responsibility for a contractor.
The reasoning and verification stages still have to assess the facts and sources.

All six guided categories with all relevant actions, monetary demand and health
signals produce at most 20 unique queries, preserving the repository batch cap.
The existing round-robin selection gives each query's first result a place within
the unchanged 20-fragment context. No extra database request, patient-text search,
model-selected source, new dependency, risk-policy change or legal approval is
introduced. Legacy query output stays unchanged. Expanded source selection can
make a previous plan's context trace stale; history remains, and existing freshness
checks must not be bypassed to keep that plan current.

Regression tests cover the three reported subject areas and dense-case selection.
A real PostgreSQL/Russian-FTS test uses explicitly synthetic approved fragments,
medical noise and pending/expired/future versions. Restoring the old planner makes
its substantive-coverage assertion fail. These tests do not prove that every
required real article exists, is approved and ranks within the live corpus, nor
that a model applies it correctly. Those are separate acceptance checks.

The distinctions were checked against official-source material, used here for
query rationale only; none is imported or approved by this change:

- Image protection: [Supreme Court determination 5-KG24-71-K2](https://vsrf.ru/lk/practice/stor_pdf/2402032).
- Workers and controlled civil-contract work: [Supreme Court Plenum No. 1](https://www.vsrf.ru/documents/all/8089).
- Defects, warranty and remedies: [Rospotrebnadzor law text](https://zpp.rospotrebnadzor.ru/npa/federal/pitanie/192115).
- Additional paid services: [Rospotrebnadzor guidance](https://zpp.rospotrebnadzor.ru/news/regional/576589).

A subsequent read-only live-corpus probe found that the broad worker-harm query
ranked labour/moral-harm material before Civil Code article 1068. Use the article's
full heading as the fixed candidate query. A PostgreSQL regression puts four
synthetic labour distractors ahead of the intended fragment under the old phrase,
then requires the planned query's first result to be the civil-liability fragment.
The query count, round-robin budget and approval/date gates are unchanged. This
test validates the ranking mechanism; live-corpus ranking remains a separate
acceptance check. The heading was checked in the
[Rospatent-hosted Civil Code](https://rospatent.gov.ru/content/uploadfiles/docs/GK-2_.pdf);
that copy is not imported or approved by this change.
