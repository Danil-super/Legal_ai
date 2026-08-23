# Specification: evidence-gated recommendations, risk policy, agents and legal updates

## Status

**Proposed — legal-editor and product-owner approval are required before implementation.**

This expands MVP phases 3–4 from `Dental_Legal_AI_TZ_v0.1.docx`. It does not approve a legal
source/version, risk threshold, LLM provider, Hermes revision or patient-facing wording. Until
the approval record below is complete, the current `ANALYSIS_BLOCKED` / `NOT_AVAILABLE` report
behaviour remains the only production behaviour.

## Immutable safety boundary

- The service helps a clinic employee; it does not supply a lawyer, send a legally significant
  response, admit liability, agree payment or file a document automatically.
- Legal Core is the legal source of truth. LLM/Hermes only receive bounded case facts and
  evidence from Legal Core; they cannot use model memory as law, browse freely, approve a source
  or version, or modify policy.
- A legal recommendation, conclusion or patient-response draft is permitted only if every legal
  claim is `VERIFIED` against a date-applicable `APPROVED` fragment. On any failure, the report
  exposes the block reason and contains neutral intake/operational steps only.
- Tenant context is server-derived. Every tenant-owned record has `clinic_id`; an agent, prompt
  or Telegram request cannot choose another clinic.
- Payments, acquirer credentials, webhooks and self-service purchases remain out of scope. Access
  stays an individual owner-granted entitlement (`MVP_MANUAL`).

## Legal base v1 — proposed composition

Only *approved immutable versions* of the following groups may later enter production retrieval.
This is a review scope, not an approval or a claim that a particular provision applies to each
case. The legal editor must verify the current official publication, effective-date boundaries,
verbatim fragments and use case before promotion.

| Priority | Proposed act group | Purpose | Current state |
|---|---|---|---|
| P0 | Paid medical services: Government Decrees No. 736 and No. 659 | information, contract, estimate, consent, quality, records, claims, responsibility | review candidates only |
| P0 | Federal Law No. 323-FZ, health-protection fundamentals | patient rights, information/consent, medical secrecy, medical documentation | not ingested |
| P0 | Law of the Russian Federation No. 2300-1, consumer protection | consumer-demand and response scenarios | not ingested |
| P0 | Civil Code provisions on paid services, performance, liability and damage | contractual/civil-law scenarios | not ingested |
| P1 | Federal Law No. 152-FZ, personal data | privacy/data-risk escalation, not patient-response templates | not ingested |
| P1 | Current official Ministry of Health acts on documentation and informed consent | inventory questions and escalation | exact acts selected by legal editor |
| P2 | Official court practice/regulator guidance | research context only | excluded until separately approved |

The initial primary-source allowlist is `publication.pravo.gov.ru` and `pravo.gov.ru`. Official
Rospotrebnadzor, Roszdravnadzor and other authority materials may be secondary review material
only after a versioned allowlist revision; they cannot alone justify a production claim. Search
snippets and model memory are never evidence.

Existing review artifacts are [Government Decree No. 736](https://publication.pravo.gov.ru/file/pdf?eoNumber=0001202305120025)
and [Government Decree No. 659](https://publication.pravo.gov.ru/file/pdf?eoNumber=0001202606010083).
Federal Law No. 323-FZ has an [official publication record](https://publication.pravo.gov.ru/Document/View/0001201111220007).
The legal editor must select the applicable current official version for every additional act.

## Approval record before enabling recommendations

1. A qualified platform-side `LEGAL_EDITOR` signs a checksum-bound attestation for every source
   artifact and fragment revision. A clinic subscriber, platform owner and LLM cannot substitute
   this approval.
2. The product owner approves incident coverage and the monetary threshold's currency, value and
   inclusive/exclusive comparison semantics.
3. A legal editor approves a new immutable `risk-policy` version and regression scenarios.
4. Security/product owner approves the exact external model, data-processing boundary, retention
   and Hermes immutable release/commit; otherwise the provider remains `DISABLED`.
5. Regression, tenant-isolation, verifier-negative and no-auto-send tests pass against the exact
   approved policy/corpus snapshots.

## Risk policy v1 — proposed conservative contract

Risk is deterministic code over typed facts. It persists a policy version, rule IDs, input-fact
snapshot hash, level and score in an immutable case-risk record. LLMs can explain a result but
cannot calculate or change it.

| Level | Proposed triggers | Required action |
|---|---|---|
| `CRITICAL` | serious harm/hospitalisation; court document; official regulator request/inspection; criminal-risk signal | mandatory human legal review; block external draft |
| `HIGH` | lawyer/representative contact; written claim; key-document conflict; demand at/above approved threshold; privacy/medical-secrecy incident | prominent legal-review recommendation; no external draft without authorised human review |
| `MEDIUM` | lower refund/compensation demand, regulator threat without official request, negative-review pressure or missing relevant documents | missing-fact/action checklist; no categorical legal conclusion |
| `LOW` | no higher trigger and all required facts/documents complete | evidence-gated internal recommendations only |
| `UNAVAILABLE` | policy-required fact is `UNKNOWN`, no approved policy or evidence/verifier failed | no risk conclusion; ask facts or escalate |

The numeric score is optional in v1. If used, weights, threshold and rounding are part of the
same immutable policy version; no business threshold is hard-coded. Policy edits create a new
version and never alter a past report.

## Evidence and verifier gate

`case-analysis.v1` freezes the fact snapshot, `as_of_date`, policy version, corpus-query trace
and structured claims. Each legal/action claim carries an ID, type, text, fragment IDs, source
metadata, verifier result and block reason. Results are `VERIFIED`, `UNSUPPORTED`,
`CONTRADICTED`, `NOT_APPLICABLE` and `INSUFFICIENT_FACTS`.

Verification passes only when each normative claim has a returned, `APPROVED`, date-applicable
fragment; source/version/date/hash match; the claim does not overstate evidence or turn unknown
facts into true; and no claim is unsupported/contradicted. `CRITICAL` cases never contain a
patient-response draft. Failure is fail-closed: the report is `ANALYSIS_BLOCKED`, while the bot
can show only missing information and escalation instructions. Audit retains claims, fragment
IDs, verifier result and template version—not raw patient text.

## Hermes / LLM boundary

Hermes is optional and disabled by default. Before activation it is pinned to a recorded immutable
release/commit and passes compatibility/security smoke tests. It receives only locally
pseudonymised minimum context: names, contacts, diagnoses, attachments and raw Telegram messages
are excluded.

Allowed tools are read-only and server-authorised: case projection, missing facts, approved legal
search at a fixed date, deterministic risk evaluation, and structured-draft submission for
verification. It gets no terminal, browser, arbitrary network, source/policy/entitlement write,
approval or patient-send tool. Each MCP tool has a versioned JSON contract and tenant/authz
negative tests. The provider adapter has timeouts, rate limits, redacted observability and a
deterministic `ANALYSIS_BLOCKED` fallback; it cannot participate in fetching, parsing, versioning,
diffing or approving law.

## Legal updater and comparison pipeline

The updater is deterministic and never promotes a version:

```text
versioned source allowlist -> discover -> immutable raw fetch -> SHA-256/idempotency
-> full parse -> structural diff -> REVIEW_REQUIRED queue -> human review
-> regression suite -> checksum-bound APPROVED promotion
```

It enforces HTTPS and exact allowed hosts. Redirect violations, content/signature mismatch,
parser/date ambiguity or outage stop the run and create an auditable review item. Raw bytes are
preserved before parsing; publication and effective dates are distinct. The diff is structural:
added/removed/changed headings, articles, parts and paragraph hashes. An optional LLM may write a
non-authoritative impact summary after parsing; it cannot alter diff/state/approval. A daily,
idempotent/backoff schedule is proposed; no live fetch happens while a user case is processed.
Any P0 regression failure, unreviewed diff or missing attestation leaves `REVIEW_REQUIRED`.

## Delivery sequence and gates

1. Approve this packet and create ADRs for legal-base scope, risk-policy v1 and the selected
   Hermes/provider boundary.
2. Add policy, risk and escalation persistence by migration; prove tenant scope and immutability.
3. Implement claims/verifier and fail-closed report rendering using approved-only retrieval.
4. Add read-only agent adapter and pinned Hermes compatibility harness; keep provider disabled
   until approval.
5. Add updater, review queue, structural diff and regression promotion gate.
6. Enable evidence-gated internal recommendations only after all approval/test gates pass. Payment
   remains absent and access remains owner-granted.

The final enablement test set includes attempted bypasses for unapproved/expired sources, wrong
date, cross-tenant case, unsupported claim, unknown critical fact, critical-risk draft,
non-owner user and provider failure.
