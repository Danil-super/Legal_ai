# Capability map: AI-first case resolution

## Status

The owner approved the two-part direction on26October2026 and clarified that the
bot must provide useful recommendations quickly, without pointless/repeated
questions. This approves capability boundaries, not an unreviewed API/schema,
risk-policy activation, implementation plan or production release.

This map extends CAPABILITY_MAP.md; existing module IDs are not renamed.

| Module id | Responsibility | Existing providers |
|---|---|---|
| `ai-first-resolution` | Decision-relevant clarification, grounded internal action plan, useful AI work product for lawyer review | `case-core`, `legal-retrieval`, `risk-escalation`, `agent-orchestration`; delivery through `telegram-gateway` |
| `escalation-policy` | Separately versioned revision of overly broad automatic escalation triggers | `case-core`, `risk-escalation` |

Build/release order: `ai-first-resolution` first, then `escalation-policy`.
That order is a safety/release prerequisite, not a circular runtime dependency.
The first module consumes current risk decisions without changing them.

Module spec: SPEC-ai-first-resolution.md, behavioral direction approved26October;
detailed AIF01–AIF11 plan/tasks were then approved ("да, работай"). Concrete
contract/storage review and release approval remain the documented checkpoints.
The second module gets its own reviewed specification before policy changes.
Keep the existing10,000RUB threshold pending that review; this initiative does
not silently increase it, reapprove legislation or remove mandatory urgent routing.

## Local status — 28 October 2026

Factual staging, optional context, draft-context authorization, immutable lawyer
plans and human review are implemented behind default-off flags. Production is
PR111 (`2940af1`): the separately approved Hermes input-boundary/security hotfix
passed CI, deployed and passed live health/context-integrity probes. The AIF
feature itself remains local; final integration/live quality gates remain open.
Local reference evaluation scenarios now have a bounded nonauthoritative model
projection, existing-workspace-grant checks and immutable provenance traces
separate from normative evidence. Final lawyer-write expiry checks, opt-in wiring
and reference-positive worker verification remain in progress. No production
analogy usage, automatic source approval or permission expansion is claimed.
