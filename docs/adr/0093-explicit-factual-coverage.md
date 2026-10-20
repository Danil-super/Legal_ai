# ADR 0093: Explicit negative facts and a closed model-facing screening schema

Date: 2026-10-20. Status: implementation contract.

## Context

The production fictional quick smoke supplied explicit health and safety facts,
but the initial interpretation omitted them and requested9follow-up answers.
The prompt told Hermes to omit negated facts, conflicting with existing NO
screening values. Its JSON schema exposed safetyScreening as arbitrary JSON,
without the permitted signal names, monetary format or version. Model omissions
must not be filled by inventing safety answers or suppressing required questions.

## Decision

Change only the initial interpreter's model-facing guidance and schema. Explicit
negative reports are factual proposals; absent information is not NO or UNKNOWN.
A negative about one event must not become a negative about other events. A
denied refund request is not proof that no other monetary demand exists. Unclear,
contradictory and missing values remain omitted; explicit uncertainty may be
represented only with the existing UNKNOWN choice. No medical causation is inferred.

Derive the model-facing safety object from Core's FactualSafetyScreening JSON
schema, moving its nested definitions into the enclosing proposal schema. Require
only schemaVersion; all factual members remain optional. Keep the existing closed
property names, YES/NO/UNKNOWN choices and positive RUB amount in kopecks. The
existing partial validator still checks monetary dependencies and contradictions.

The coverage instruction considers every supported field in one existing call,
including the user's stated response purpose, which is not a recommendation
invented by the model. There is no second completion or automatic retry to force
the model to populate a minimum number of fields.

The public/internal wire contracts, Core validators, grounding, optional omissions,
source/proposal fingerprints, provisional staging and final summary confirmation
are unchanged. No new data category, table, provider/model/effort, risk policy,
access rule or normative approval is introduced. Core remains the legal authority.

## Alternatives and consequences

Defaulting missing safety facts to NO or bypassing clarification would understate
risk and is rejected. A regex extractor for natural-language negation would make
fragile semantic decisions outside the existing interpreter. Changing the shared
wire model to a new partial model would broaden this correction unnecessarily.

Prompt/schema tests establish the instructions and structure sent to the model,
not model quality. Complete-proposal integration tests establish that preserved
facts skip the corresponding questions. Current-profile fictional live diagnostics
must measure extraction and remaining question identities before release; neither
they nor READY reports establish correctness for every possible legal situation.
