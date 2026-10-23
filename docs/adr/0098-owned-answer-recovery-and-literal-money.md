# ADR0098: Recover current owned questions and parse literal amounts

## Status and scope

Owner-requested incident correction,23October2026. No model/profile, risk policy,
approval, tenant permissions or patient-delivery changes. Release remains separate.

## Incident

The amount operation failed MODEL_PROTOCOL_INVALID. Core correctly omitted failed
work from discovery and did not save the amount; Gateway required discovery to
rediscover the failed operation before any action, creating a dead end. The same
trap occurs if staging committed but both HTTP replies were lost.

## Decision

On an obsolete-result error, independently authorize and re-read the requested
draft, preserve the active-draft fence and positively establish no current live
answer operation via successful discovery. Only then resume those current facts
or navigate to the menu. Never infer success/cancellation of the old operation,
modify facts from a revision increment, cancel another operation or bypass an
authorization/transport failure. If discovery is unavailable, show a draft-reopen
and menu action instead of recursively repeating broken operation controls.

Literal positive ruble scalars on the server-bound amount question may be mapped
to integer kopecks without an LLM call. Privacy, source/currency/range validation
remains the existing Core validation; proposals remain unconfirmed draft values
until the one final summary consent. Free-form, uncertain, multiple, negative,
foreign-currency and other-question answers retain their existing interpretation.

## Verification

Actual PTB composition/watch tests reproduce FAILED and lost-stage-reply recovery
for refresh/edit/manual/menu, plus no-commit retry. Agent tests prove exact money
does not call the provider and unsupported/ambiguous values are not fast-parsed.
Additional authorization/race checks and full gates remain release requirements.
