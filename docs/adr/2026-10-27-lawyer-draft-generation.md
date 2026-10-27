# ADR: Evidence-grounded claim proposals for lawyer review

27 October 2026, AIF07 generator-only slice. No endpoint, producer wiring, storage
write, permission, model/provider/profile configuration or production activation.

`LawyerDraftGenerator.generate` reparses the private `LawyerDraftContextResponse`
into plain validated values before external model I/O. Its source binding must
match the actual analysis context. It uses the existing pseudonymized projection;
operation/escalation identifiers and snapshot hashes are not model input. Core is
responsible for obtaining current authorized, approved/date-applicable evidence.
The typed bundle does not itself prove legal validity or authorize its caller.

The generator calls the existing `LegalReasoningOrchestrator` with the explicit
keyword-only `lawyer_review_draft=True`. The default remains false and retains the
ordinary research/review prompts and result behavior. Both existing Hermes clients
are reused unchanged; a supported draft still uses one researcher and one reviewer
call. No alternate service, reference examples, memory, tool or model is added.

The special research purpose requests an ordered concrete internal plan, options
and conditions grounded in supplied evidence and known facts. It prohibits liability
admissions, medical prescriptions and patient-directed text. Every LEGAL/ACTION
proposal retains its exact citations and real fact dependencies. Unknown facts do
not become negative/default facts. An unsupported claim or action should be omitted.

Before the reviewer call, draft mode rejects nonempty patientDraft or free-form
internalRecommendations, more than eight ACTION claims, blank/over-600-character
actions, a nonempty claim set with no actionable plan, and invalid/repeated/scoped
clarification requests. It never truncates the meaning to fit storage. Empty claims
remain valid evidence-based abstention, carrying only permitted typed clarifications;
that path uses no reviewer call and is not a fabricated completed plan.

The result remains the existing `ReasoningResult`: unapproved claim proposals and
independent semantic reviews. SUPPORTED is a model judgement, not VERIFIED, approval,
analysisAllowed, a report ID or permission to deliver a patient response. Unsupported
reviews and unknown requiredFactKeys remain intact for the later Core verifier.
This module does not build or store an approved InternalWorkProduct.

The generator recreates transport/protocol failures without input-bearing exception
causes; structured Hermes failure reasons remain available. Invalid nested/context
models are rejected before the first request. Existing projection/privacy checks
remain required; the punctuation-email regression also gates acceptance of this slice.

Validation uses synthetic HTTP MockTransport responses through real Hermes clients,
not claims about live provider speed or legal quality. Tests cover source binding,
forged nested models, pseudonymization, exact citations, both profile calls, unknown
dependencies, service clarification scope, action budgets, abstention and timeouts.
Core writer verification, durable delivery, legal quality evaluation and release
approval remain separate tasks.
