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
still PR110. Final integration/live quality gates and release remain open.
Reference evaluation scenarios are not yet in the model projection; no analogy
usage is claimed. Their bounded nonauthoritative use must preserve existing
workspace grants and bind current approved scenario provenance separately from
normative evidence. No automatic source approval or permission expansion follows.
