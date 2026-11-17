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
