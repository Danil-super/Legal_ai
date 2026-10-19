# Live intake screening: model value shape

## Observed production acceptance failure

Main revision `eea1af99ef6f7c77842f11cd00aec2854394da80` passed CI, including
4,028 PostgreSQL/MinIO integration-matrix tests without skips, and deployed successfully.
All eight routine services were healthy. Migration `d10a6b3e7f91` applied; the legal
approval and reference-review ledger counts and digests matched their predeployment baseline.

After owner-authorized activation of the complete pre-report interpreter, a fictional
owner-only quick intake passed initial understanding and two factual interpretations.
Its next safety answer stopped at `MANUAL_CONTINUATION`, with metadata-only audit reason
`MODEL_PROTOCOL_INVALID`. No case/report was created by this attempt.

A single read-only replay of that fictional input through the unchanged researcher profile
returned a `PROPOSED` interpretation with valid source offsets, but its `value` was an object
instead of the required scalar signal. The broad cross-family `JsonValue` schema in the model
prompt allowed that object, while the current-question runtime validator correctly rejected it.
No real patient content or raw model text is included here.

## Scoped correction

For a non-amount safety question only, narrow the prompt's value schema to the signal enum
or null, and explicitly explain that the value is not a screening-field-keyed object.
Null remains available for `UNRESOLVED`; the existing status/value validator still applies.
Other question families and monetary objects retain their existing schema.

No output unwrapping, extra retries, runtime-contract relaxation, model/provider changes,
risk-policy changes, approval changes or automatic patient messaging are introduced.

Regression checks cover all seven signal fields, YES/NO/UNKNOWN and unresolved answers,
rejection of a nested screening object, and absence of schema mutation in subsequent monetary
or ordinary factual requests. Four targeted schema tests failed against the original prompt
before the correction; the nested-object rejection already passed.

## Acceptance still required

The original live attempt is not a successful end-to-end acceptance. After independent review,
green CI and normal deployment of this correction, repeat both fictional intake paths through
confirmed facts, analysis and owner-only PDF delivery. Telegram intake transport in this harness
is synthetic; Core, PostgreSQL, Hermes and final owner delivery are real.

The 67 reference evaluation cases remain DRAFT; this correction neither approves nor activates
them. A passing synthetic report is not certification of all legal answers.
