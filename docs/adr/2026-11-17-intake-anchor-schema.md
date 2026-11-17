# Expose the existing intake anchor count to the model

Date: 2026-11-17

The functional audit reproduced a native initial-intake failure on a fictional
description containing a service date, complaint date, monetary demand, health
consequence and explicit negative reports. The proposal correctly identified
the dates and service but supplied four source intervals for safetyScreening.
Core rejected it with MODEL_PROTOCOL_INVALID: each field accepts one to three
intervals. That model-validator constraint was absent from the generated JSON
Schema, so a response could satisfy the advertised schema and fail Core.

Add minItems=1/maxItems=3 to each sourceSpans array in the private provider
schema. Explain that multiple facts in one field can share wider contiguous
source intervals without dropping supported facts. Do not truncate, repair,
auto-confirm or silently discard the model's proposal. Core's validation,
source binding, empty-proposal abstention, user confirmation, model, reasoning
mode and deadlines remain unchanged. No additional model call is introduced.

The schema regression fails before this correction. Real streaming-client
tests preserve rejection of zero/four anchors and acceptance of one/three.
Independent review approved the fix; removing the bounds in memory makes the
schema test fail. Native candidate and post-deployment protocol checks remain
required evidence before declaring the initial AI flow working.
