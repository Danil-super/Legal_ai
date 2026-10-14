# Source-grounded case acceptance — 2026-10-15

Scope: owner-authorized fixed fictional clinic probes. This is runtime evidence,
not full legal certification or patient advice. No approval/source/risk/role,
model/provider/high or timeout-budget changes were made in this increment.

## Released clarification repair (PR98)

Main4db0c70, CI38635701182 success; eight services healthy. Full2494 tests passed.
The Core now adds a missing service-date question only for its actual unknown-date
gate, with the same maximum three questions; attempted UNKNOWN/APPROXIMATE answers
are never silently replaced or asked again.

Fresh guided case29c650bc-366f-44f3-baa8-7fdf1c6da332:
job4690ea24-6747-4d16-80fe-1cb766bd021a succeeded63.34s;
report343d21fa-f22b-4e22-ba7d-f63ecd38f244 READY/PASSED, LOW/v4.
Verified receipt action cited approved659 point48. PDF30522 bytes SHA verified,
real owner Telegram delivery737.

Fresh quick case1579a7b4-9d62-4adf-83f6-6fc8e8bd03a1:
jobf800c492-8428-4d31-a602-3dd3232a035f succeeded48.29s with two fact questions
and no advice. Actual gateway report-bound recovery confirmed PAID/exact2026-10-14
in new immutable casedb4176d8-3664-4350-a0eb-16193b2cf97b.
Jobaf74e040-2c39-42f1-b86c-baef05374cbc succeeded63.37s;
reportfc4a5939-fa5a-465f-856d-712b846acb0d READY/PASSED, LOW/v4;
two verified LEGAL claims supported by659 points34/48, but no ACTION claims.
The generic staff-review fallback was a plan-quality gap, not useful-plan acceptance.
PDF31480 bytes SHA verified, real owner delivery740.

Truthful UNKNOWN case41833105-323e-4935-a7cc-162b1d48ca5e:
first jobe14afdf0-fa48-44ae-a2c4-42bdf91f421f failed ANALYSIS_PROVIDER_INVALID.
Safe logs showed APIError and orchestrator502; no specific upstream quota/status
was established for that failure. A read-only diagnostic did not submit a result.
Bounded operator retry job8997dfb4-41d7-4e8c-95b6-92ad37274299 succeeded42.24s;
report9cc00e8e-c879-4bbd-b29b-6aa21c0fe341 BLOCKED/EVIDENCE_NOT_VERIFIED,
no repeated questions, advice, sources or patient draft. PDF28011 bytes SHA verified.
This persisted negative is valid; the preceding provider failure remains a failure.

## Useful action producer (PR99)

Reviewed headbcfdcf4f25d917c0946c925e096cde71d9821186 asks only essential
source-grounded ACTION claims, with exact evidence UUIDs and honest dependencies.
No raw internalRecommendations are published. No forced action count or LEGAL to
ACTION relabeling. AFFECTED_SERVICES applies only to GUIDED_V2; legacy preserves
actual SERVICE_TYPE/SERVICE_DATE dependencies. Core gates unchanged (ADR0090).

Prompt regressions were observed RED before correction. Full PostgreSQL/MinIO
regression2501 passed, zero skips; Ruff,139-source mypy and Compose config with
synthetic env passed. Fresh isolated independent review110 focused passed, no
Required/Critical findings. Temporal-gate mutation was caught by four negative
ACTION cases; restored. Prior Required legacy regression was fixed before release.

PR CI38640953397 and main CI/deploy38641834332 succeeded. PR99 merged as
b052f403982215c15b2b9d317210e94803ee89a7; server confirms that revision and eight
healthy services. Only the changed orchestrator needed recreation in this deploy.
The approval ledger still matches54/8 and both prior equality digests.

Prepared quick-recovery case3bf8cf86-149c-4c8a-b9c1-a1d00cb9123a:
jobd03c7a1d-8239-4a98-943c-a16c978f81b1 succeeded63.34s;
report8166e6f8-a552-4ea2-a3d9-8eb6d82672a3 READY/PASSED, LOW/v4, no questions.
Two concrete verified actions (receipt and contractual quality review) cite659
points48/34; no generic fallback, established-defect claim or liability admission.
PDF31434 bytes SHA verified, owner delivery745.

Fresh guided case31be5f64-98e0-4128-8b74-7402606243c3:
job6072dcf2-80ec-4e33-b871-c7e71860dd8d succeeded54.31s with a slot0 date
question, reportcfb7ad47-5ddf-45fe-a4a9-d147b7fe51a4, no legal advice.
Gateway recovery confirmed exact2026-10-14 in new case2003d889-bc16-49d2-8817-4ab816369584;
job7a2dc4a4-f7bd-4010-8ad3-b16f24728395 succeeded75.40s;
report7fe52f9b-6e36-4c29-9c51-6f960a4b90d5 READY/PASSED, LOW/v4, no repeated
question. Two concrete verified actions cite659 points48/17 (receipt and work-hours
information for arranging the already-offered examination). No medical prescription.
PDF31349 bytes SHA verified, owner delivery748.

Fresh quick case369d3f32-d69d-459e-8451-8a4493f614f6:
jobecdb47d4-d234-4205-acdb-3e941ad31872 succeeded48.44s with payment/date
questions; report46d54e13-8bd8-49a9-a0d6-919c7a0889b6 withheld advice.
Gateway confirmation created case2bba657a-9fc9-4158-b569-8204cf020932, not a
mutation of the original. Jobab68b1fd-e04f-40c1-9603-ebe2ad3bf471 succeeded75.42s;
report2ce38555-ad13-40dc-bdcf-35c2f023cfb3 READY/PASSED, LOW/v4, no questions.
Two concrete verified actions cite659 points48/34 (receipt and quality review),
with explicit limits on absent medical-quality criteria. No generic fallback.
PDF31308 bytes SHA verified, owner delivery751.

Re-enqueueing the prior UNKNOWN case returned its existing job8997dfb4 and
report9cc00e8e in0.02s (idempotent replay), not a new provider run on PR99.
That replay is not counted as fresh negative acceptance for the new producer.

Fresh UNKNOWN case253243f3-c3d7-4f4a-9f38-25a3cf373b0c:
job5a8ab75c-db84-4271-8aec-bc9a7d5bd307 succeeded54.30s with payment/date
questions and no advice, report1deac18a-40e6-47f5-a600-b07851969ef1.
Real gateway recovery confirmed UNKNOWN payment/date in new immutable case
30e71560-90ea-48ed-a421-fab33138f496. Job63a39a4b-a258-487d-9f2d-b8c3d4ef7281
succeeded51.28s; reportb176fd94-0808-45d4-a082-16c2f5bfbdaf
BLOCKED/EVIDENCE_NOT_VERIFIED, no repeated questions, recommendations, sources
or patient draft. PDF28011 bytes SHA verified. This is a fresh PR99 provider/
Core-persisted negative, not the prior cached report.

All seven fresh PR99 analysis jobs above succeeded without operator retries;
three positive plans, three clarification-only results and one safe UNKNOWN block.
That sample does not erase earlier transient provider failures or prove zero
future failures. Positive analysis time was63–75s, clarification48–54s; a case
requiring clarification consequently takes more than one analysis round.

Read-only source comparison checked the actual approved659 fragments17/34/48
against the published actions. As an additional metadata/body cross-check, the
[government clinic's published text](https://dgp148.mos.ru/itemsdocuments/ppr-postanovlenie-ot-30-maja-2026-goda-659-ob-utverzhdenii-pravil-predostavlenija-medicinskimi-organizacijami-platnyh-medicinskih-uslug/)
and [Rospotrebnadzor notice](https://zpp.rospotrebnadzor.ru/news/federal/576535)
confirm the document identity and commencement. They do not replace Core evidence,
modify the trusted-source register or expand lawyer-copy historical coverage.

Read-only candidate diagnostics were not Core acceptance: the first successful
reasoning attempt hit an operator serializer AttributeError while printing its
dataclass, not an app/provider failure; the corrected diagnostic produced two
actions and no questions without submitting. Live persisted jobs above, not that
diagnostic, establish useful-plan acceptance. No automatic retry/model budget changed.

## Boundaries

Intermediate Telegram input/ACK in the operator harness is simulated; actual
gateway composition, queue, Core, Hermes researcher/reviewer, persisted report,
PDF download/checksum and final owner delivery are real. This is not a manual
phone-click test or unrestricted proof of every scenario.

Both models remain gpt-6.1-sol/high, request/stale55 and unchanged outer budgets.
External provider interruptions have not been eliminated. The67 reference cases
remain DRAFT and outside online reasoning; no automatic legal approval was made.
The tested lawyer-copy source applicability starts2026-10-08; no earlier edition
or effective date was invented. No automatic legal message was sent to a patient.
