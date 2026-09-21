# ADR 0041: Preserve distinct deadline questions in sparse intake

Date: 2026-09-21
Status: Accepted

## Problem

The proposed guided intake skips already populated fields. The v1 draft has one
response_deadline field shared by three distinct sources: a written claim, the
patient's representative, and a court/authority document. A populated aggregate
must not silently satisfy a later source's deadline question. The wire mapper also
previously omitted RESPONSE_DEADLINE in representative-only cases even though it
required that value locally, causing Core to ask for the already answered fact.

## Decision

Retain the existing v1 contract and sequential wizard. The next-question function
accepts the just-completed wizard state. Active deadline questions after that
cursor are mandatory regardless of the existing aggregate; ordinary confirmed
fields still skip. Subsequent sources retain the earliest known user-supplied date,
using the existing aggregation rule. Invalid input does not advance the cursor or
replace the earlier answer. The next cursor and aggregate are saved together by
the existing versioned draft endpoint, so resuming a draft needs no transient
'asked deadline' flag. There is no new table or new user category.

The presentation explicitly calls this the nearest user-supplied deadline, not a
statutory calculation. UNKNOWN is accepted as an explicit answer, never replaced
with today's date. If all supplied deadlines are unknown the aggregate remains
UNKNOWN. The legacy v1 aggregate does not preserve every source's date or indicate
that another source was unknown when one source is known. A future per-source
chronology needs a separately reviewed contract; this patch does not claim it exists.

The mapper emits exactly one RESPONSE_DEADLINE for every combination of deadline
sources, including the representative-only case and legacy boolean signals. Core's
existing completeness, date applicability, risk and tenant checks stay unchanged.

## Verification

Parameterized tests cover all eight source combinations with known/unknown dates,
source order, canonical request validation, legacy boolean signals, invalid input,
menu/resume and a fresh application reload from a saved cursor. Disposable
PostgreSQL tests submit representative-only and combined-source cases, including
unknown deadlines, through the real authorized API and check workflow replay.
The existing gates remain mandatory; no test is skipped or weakened to publish.

## Related usability fixes

Fix grouped-ruble formatting without float conversion. Reject malformed legacy
date objects as missing instead of accepting an 'EXACT' string containing 'unknown'.
Resolve Ruff/Mypy issues without suppression or changes to the quality gate.
