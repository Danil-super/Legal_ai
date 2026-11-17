# Lawyer-feedback evaluation

All cases below are fictional probes based on the reported topics. They are not
copies of patient files and do not reconstruct facts missing from the feedback.
Use the existing models/high configuration and actual approved date-bound corpus.
Record the source revision, facts, selected source identifiers, reference-example
count in both model passes, elapsed time, verification and complete answer.
Keep unsuccessful attempts. No item below is marked passed merely because its
theme appears in a prompt or because a PDF was generated.

## Cases and content checks

1. **Caries treatment and a 5,000-RUB refund.** Fictional treatment on 10 November
   2026; first oral complaint on 16 November: the filling fell out, refund sought,
   examination offered; no hospitalization, reported deterioration or authority
   documents. Check grounds for the remedy, defect/causation and warranty evidence,
   the significance of examination and signed/payment documents, alternative
   outcomes and concrete next actions. Do not assume the complaint proves a defect
   or promise a refund. Registration and deadlines alone fail this case.
2. **An image published without reported consent.** On 16 November 2026 a patient
   reports that the clinic's social-media account contains an identifiable image
   and treatment details; a contracted SMM specialist posted it, consent is not
   available, and 5,000 RUB compensation is requested. Check image protection
   separately from personal/medical data; preservation/removal, consent evidence,
   grounds and assessment of compensation, the contractor's actual role and
   possible clinic responsibility. Do not infer that contractor status alone
   excludes responsibility or fix compensation without supporting facts/sources.
   Inspect whether the supplied image source includes Civil Code article 152.1.
3. **An additional paid service.** On 10 November 2026 a fictional clinic performed
   an additional diagnostic service and billed 2,500 RUB; on 16 November the patient
   disputed its price and authorization. The patient's account does not establish
   whether prior agreement or disclosure existed. Check the signed scope/price,
   information and consent to the additional service, billing evidence, conditional
   payment/refund outcomes and a practical proposed resolution. Do not decide
   consent, debt or a refund from a template or the category alone.

## Intake and provenance checks

- A valid semicolon-separated list advances the services question, including an
  unresolved/unavailable model path; invalid or identifying input explains how to
  correct it. No automatic fact confirmation or case creation is introduced.
- FINANCE works in initial text, follow-up interpretation and button selection.
  It adds no medical-service question by itself. Known confirmed answers are not
  asked again; an approximate/unknown answer remains uncertain rather than false.
- Both model passes receive the same allowed, relevant approved compact examples.
  Reference answers illustrate a method; they never enter normative citation IDs.
  Absence of workspace grants, suitable examples or evidence is reported honestly.
- PDF, clinic summary and lawyer plan have one list number per item. Rendering
  preserves stored wording, hashes, citations and human-approval bindings.

## Decision record

For each content check record **supported**, **missing**, **contradicted** or
**unverified**, with the answer excerpt and source identifier. Review correctness
and usefulness, not wording similarity. A structural PASS cannot replace this
inspection. Final legal acceptance belongs to the reviewing lawyer; do not label
an unreviewed result legally certified or promise error-free operation.

## First read-only live checkpoint

Candidate d16a785 used the current production-approved corpus, three approved
examples in each case, the existing native models/high, and the normal two model
passes. The model inputs contained identical example hashes in both passes. Core's
claim verifier allowed all three resulting batches. No case or approval was
written and Telegram delivery was not exercised.

| Fictional case | Initial interpretation | Two reasoning passes | Content gap found |
| --- | ---: | ---: | --- |
| Caries refund | 41.380 s | 90.801 s | Better grounds, warranty, evidence and alternatives, but articles 24/31 were absent from selected evidence. |
| Image/privacy | 46.691 s | 146.861 s | Image and contractor issues addressed; general moral-harm grounds were absent and the model correctly abstained. |
| Additional service | 53.701 s | 97.939 s | Grounds distinguished unpaid billing from refund, but key document checks were left in assessment rather than concrete actions. |

The initial caries interpretation preserved both dates and paid-service basis;
some explicitly grouped negative reports were omitted in these probes. These
are deficiencies to fix and retest, not a final legal-quality acceptance. The
privacy timing also leaves insufficient room for Core within the former
150-second total case wall. The separate bounded-budget correction must be
verified in the full workflow before release. Private complete answers, source
identities and failed-attempt evidence are retained outside the repository.

## Follow-up read-only checkpoint

The second checkpoint used candidate 1bf630e, the same current approved corpus,
three allowed examples per case and unchanged native models/high. All selected
refund contexts now contained consumer-law articles 24, 29 and 31; the privacy
context contained Civil Code articles 151, 152.1, 1068 and 403. The initial
interpretations retained all seven explicitly reported safety fields and the
known event dates; the billing-only case did not become a clinical dispute.

| Fictional case | Initial interpretation | Two reasoning passes | Observed content |
| --- | ---: | ---: | --- |
| Caries refund | 44.462 s | 108.666 s | Four assessments and five actions cover defect/warranty/burden, amount, deadlines, document/examination checks and conditional remedies. |
| Image/privacy | 54.924 s | 123.884 s | Five assessments and nine actions cover image/data/confidentiality, moral harm, contractor role, evidence, removal and conditional settlement. Some sentences require grammatical editing. |
| Additional service | 54.266 s | 96.949 s | Three assessments but only one registration action: practical-plan coverage FAILED. |

A stronger generic completeness instruction in candidate 16af70b did not fix the
billing gap (45.747 s initial, 97.319 s reasoning, still one action). This failure
is retained. Candidate 711798c clarifies that unknown consent permits a
source-supported conditional document check and that a billing dispute alone
must not require clinical-service facts. Its next live output took 43.454 s for
initial interpretation and 73.901 s for both reasoning passes, with three
assessments and four actions: record the request; compare signed scope, price,
consent and payment; choose cancellation/partial cancellation or documented
objections conditionally; distinguish unpaid cancellation from a paid refund and
its applicable deadline. It returned no clarification requests, and Core allowed
the source-bound claims.

These are observed content checks, not a legal certification or a latency SLA.
Both model passes received the same three approved compact examples (matching
input hashes); no model weights were trained. Selection retains existing
workspace grants: approval of the 67 examples does not grant every employee
access. These probes performed no case writes, report persistence, Telegram
button interaction or delivery. The separate workflow/release gate remains open.

## Released workflow and focused follow-up

Main 9fe0300 passed release CI/deployment and the actual saved-draft → v2 workflow
→ live worker/model passes → Core → persisted READY report/PDF → own history
path. Fictional case DL-2026-000040 completed in 114.827 seconds from enqueue;
the downloaded PDF matched its stored SHA256 and contained no double numbering.
This used the working Core API, not manual Telegram button interaction.

Fresh source-equivalent model probes covered caries (109.979 s), image/privacy
(133.761 s) and billing (78.957 s). Two interrupted diagnostic probes are retained:
the diagnostic SSH tunnel timed out during deployment; their incomplete streams
were rejected and did not produce reports. Renewed connections completed both.
All 67 reference evaluations remained approved; the owner and active lawyer each
could select three examples, while the revoked lawyer remained unauthorized.

Independent reading still found a privacy plan without explicit preservation
before hiding/removal. The focused follow-up requests a source-supported internal
record of publication content/address/known time/access, with restricted access,
no delay of cessation, no unlimited retention or republication, and no raw data
sent to the bot. The genuine follow-up at eaea90e completed both reasoning passes
in 124.269 seconds: six assessments and nine actions, including that preservation
step. All 15 citation sets agreed across selected evidence, semantic review and
Core verification; no clarification was requested. Unchanged initial extraction
was explicitly not rerun in that focused probe. Minor grammatical omissions still
require editorial attention; no legal certification is claimed.

The saved PDF exposed a separate display bug: extraction paths such as
article:29/excerpt:34 were shown as unrecognized locations. The presenter now
ignores only the terminal numeric extraction index and shows the actual article
or point; document:text is labeled as document text, never an invented article.
Four extracted-PDF/Telegram cases failed before the fix and passed afterwards;
unknown/malformed paths retain the existing fallback. Stored reports, source
paths, evidence hashes and approval bindings are unchanged. Existing saved PDF
bytes remain immutable; the new labels apply when a new PDF is generated.
