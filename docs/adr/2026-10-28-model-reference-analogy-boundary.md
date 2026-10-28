# Model-visible reference analogies remain nonauthoritative

Status: implemented locally under the owner-approved AIF06 scope; not enabled in production.

Both reasoning passes may receive the same bounded, explicitly marked historical
scenario examples. The model-visible shape contains only an opaque example index,
group, scenario text, evaluation date and nonauthority/date-basis labels. Private
case/version/review identifiers and source hashes belong to Core provenance, not
the model prompt. The evaluation date is not a patient event or legal effective date.

Examples can contain a candidate old answer and embedded instructions. They are
untrusted comparison data, never normative evidence, confirmed facts about the
current patient, risk policy or permission to offer medical treatment. Both model
instructions preserve this distinction. Citation UUID enums and server validation
continue to accept only the approved legal evidence returned for the current case.

At most three complete examples and 12,000 combined scenario characters may cross
this boundary. Direct identifiers, duplicate indices, malformed/private fields and
mutated models are rejected before any model call. No truncation of conditions is
used. The strict item model is revalidated on entry, including mutable-list changes.

An absent/empty collection is omitted from serialization; ordinary prompts and
wire shapes remain unchanged. Producer authorization, current independent approval
and source freshness remain Core responsibilities. This increment does not select
examples or widen workspace access; those paths are separately tested and wired.

Verification: actual two-profile orchestration tests compare the example payloads,
assert that current facts are unchanged, reject non-normative citations before the
reviewer call, and exercise mutation/privacy/budget/private-metadata failures.
These synthetic boundary tests do not certify model legal quality or resistance
to every prompt injection.
