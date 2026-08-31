# Specification: free pilot access and reviewed practical-case library

## Status

**Proposed — requires product, legal and security approval before implementation.**

## Objective

Offer selected clinic administrators free initial access while building a reliable assistant for
dental-incident handling. The service may learn recurring *scenario patterns* from reviewed,
lawfully obtained material, but only Legal Core's date-applicable, `APPROVED` official sources
can support a legal recommendation.

The pilot is not a legal-service marketplace, does not replace a lawyer and must not send a
legally significant message to a patient automatically.

## Product boundary

### Free pilot entitlement

- Add a time-limited `FREE_PILOT` entitlement only through the existing platform-owner grant
  flow. It remains user- and clinic-specific; there is no open self-registration in this slice.
- A pilot participant may create a pseudonymous case and receive intake, missing-fact and
  evidence-gated internal recommendations once all existing legal gates are approved.
- Before that approval, the current `ANALYSIS_BLOCKED` report remains correct behaviour; free
  access must not bypass corpus, risk or verifier gates.
- Public/open registration, invitation codes, additional identity data, new rate limits and new
  consent flows are separate product/security decisions.

## Practical-case library boundary

### Roles of data

| Material | Permitted role | Prohibited role |
|---|---|---|
| Official statutes and approved official fragments | sole source for a legal claim | model-memory substitute |
| Official court/regulator decision with verified provenance | reviewed research/evaluation context | automatic authority for unrelated case |
| Public legal forum discussion | discovery of recurring scenario patterns only | scraped corpus, production legal evidence, training data |
| Licensed/partner-provided, de-identified case | human-reviewed scenario and regression test | unreviewed model fine-tuning input |
| Clinic pilot case | that clinic's protected case only | cross-tenant training set or external prompt without approval |

### Acquisition rule

No crawler, bulk export, copy/paste corpus or model training may use public consultations unless
the platform and every relevant rights holder provide a written licence that covers the precise
use, storage, derivative scenario creation and model training (if any). A public page being
reachable is not a licence. For example, Pravoved.RU says its use is governed by its legal
documents and notes that rights in content in sections of the site may belong to users or other
persons: <https://pravoved.ru/policies/terms/>.

The initial practical library must be built from one of these sources, in order:

1. scenarios authored for the project by the legal editor;
2. written partner licence with documented provenance and rights;
3. individual public material only after written permission, review and transformation into a
   de-identified scenario without retained source text.

Each admitted scenario records its provenance/rights decision, reviewer, capture date, scenario
taxonomy, applicable official-source IDs, expected safe actions, escalation condition and expiry
review date. It stores neither raw forum answer nor patient/author identity.

## Review and evaluation protocol

1. A researcher may propose a scenario category (for example: treatment-quality complaint,
   refund demand, documentation request, consent/document gap, privacy incident).
2. A `LEGAL_EDITOR` removes identifiers, rewrites the facts in neutral form and links only
   current official legal fragments applicable to the scenario date.
3. A second reviewer accepts/rejects the expected action checklist and risk/escalation outcome.
4. The scenario becomes a regression case only after both reviews and has no authority to create
   a production claim itself.
5. Any source or policy change reruns the regression suite; a failed P0 case blocks promotion.

Fine-tuning is explicitly deferred. It may be evaluated only after a separate licence/consent,
data-processing, retention, de-identification and model-evaluation decision. The first model
integration uses approved retrieval plus deterministic verifier/risk gates instead.

## Security and privacy boundary

- Bot input can contain personal and medical information. It stays tenant-scoped, is minimised,
  is never logged into the research library and is never sent to a provider before the approved
  pseudonymisation/data-processing boundary.
- External pages and their text are untrusted input. They cannot instruct an agent, change a
  source/policy/version, select a tenant or supply legal evidence.
- A future source connector uses a fixed HTTPS host allowlist, no redirects, size/time limits,
  raw-artifact hashing and auditable failure codes. It never accepts a URL from a Telegram user.
- The platform-side legal review must account for the health-care context; the initial corpus
  already identifies the official publication of Federal Law No. 323-FZ:
  <https://publication.pravo.gov.ru/Document/View/0001201111220007>.

## Success criteria

- `FREE_PILOT` is enforced by the same user+clinic entitlement guard as paid access; no payment
  data or public bypass is introduced.
- Every user-visible recommendation remains blocked unless its claims pass approved-only,
  date-aware retrieval and verifier checks.
- No public consultation text enters repository fixtures, logs, Legal Core evidence or an LLM
  prompt without a recorded licence and review.
- Scenario regression tests are synthetic/de-identified and include expected escalation/blocks.
- A pilot's feedback can improve taxonomy and evaluation cases but cannot silently change law,
  policy or another clinic's data.

## Required approvals before implementation

1. Product owner confirms controlled free pilot (owner-granted `FREE_PILOT`) rather than open
   public registration.
2. Platform-side legal editor approves scenario taxonomy, reviewer criteria and source-use terms.
3. Security/product owner approves any partner/contact, external collector, LLM provider or new
   personal-data category before it is connected.
