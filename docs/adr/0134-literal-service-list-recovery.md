# Recover the explicitly requested service-list format

Status: implemented, 2026-11-17; production activation is a separate release gate.

The services question asks for semicolon-separated names, but production sends
that answer through optional model interpretation. An UNRESOLVED result keeps
SERVICES unanswered; a second literal list was also blocked by the retained old
UNRESOLVED operation. These paths reproduce with fictional inputs, actual PTB,
Core HTTP and PostgreSQL; the exact lawyer input was not supplied.

Recognize the explicit list using the existing literal-answer path and strict
current-question/value/source validation. Names remain bounded to 1–5 unique
non-URL labels, 2–120 characters each, without direct identifiers. Preserve the
existing model call when retained narrative has other material extraction targets.
A valid literal primary can then recover an UNRESOLVED model result or typed
availability failure. Preserve and revalidate independently grounded additional
facts; invalid model protocols and corrupt bindings still fail closed. No guessed
secondary values or UNKNOWN answers are added.

The gateway validates malformed formatted lists before reserving model work and
shows the existing precise validation error. Only a source-bound UNRESOLVED
SERVICES operation with no provisional facts can be cancelled and replaced by a
new validated literal list. Pending/ready operations and unresolved additional
facts keep their existing review protection. Cancellation uses the existing
bounded lost-response recovery, and old delivery watchers stop before replacement.

All values remain provisional draft content. Core rechecks tenant, actor, lease,
revision and source bindings, and only final summary confirmation creates case
facts. No legal approval, risk policy, model profile, timeout or role changes.

Tests cover literal boundaries, unavailable/unresolved models, retained dates,
invalid anchors and lists, precise retryable gateway errors, pending/ready guards,
and complete new/previously-stuck PTB → Agent → Core/PostgreSQL progression without
premature cases or reports. Model replies are simulated; no live legal-quality or
production delivery guarantee follows from these regressions.
