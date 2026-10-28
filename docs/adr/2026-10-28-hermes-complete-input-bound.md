# Preserve complete bounded inputs through the pinned Hermes adapter

Status: implemented locally; production recreation and live acceptance pending.

## Problem

`HermesClient` already accepts user messages up to120,000characters and system messages
up to20,000. The pinned Hermes commit `5fc308a70719a83cccdbba4c0e39c23f5a8239d5`
silently slices scalar messages at65,536characters in both `_normalize_chat_content`
and `_normalize_multimodal_content` in `gateway/platforms/api_server.py`.
An expanded approved-evidence projection exceeded this limit. With sorted JSON,
trailing facts could disappear from the research input; changing field order merely
moved the loss to other facts, evidence or reviewer claims.

## Decision

Extend the existing process-only guarded launcher. Before the CLI starts, verify the
original shared constant is exactly65,536 and both normalizers have the reviewed
source SHA256 of the pinned commit. Only after all checks pass, set their shared
module constant to120,000 in this interpreter. Drift aborts startup with a fixed
message and no source, configuration or payload in the error. The existing SSE
completion-status guard still installs before the CLI executes.

The existing application input bound and over-limit rejection stay unchanged.
This does not alter the provider, model, reasoning effort, tool-free restriction,
call deadlines, source selection, evidence verifier, risk rules or rights.
Do not reorder JSON as a loss workaround, truncate legal fragments, remove claim
conditions, split inputs to bypass the normalizer, or interpret truncated results
as a valid evidence-based abstention.

## Proof and rollout

Exact pinned normalizer excerpts in synthetic tests reproduce loss before the fix
and preserve the full payload at65,536,65,537,81,555 and120,000characters afterward.
Tests cover drift in either normalizer/constant, no partial mutation on drift,
in-memory-only changes, startup ordering and application rejection of120,001 before
transport. Existing completion, tool/call-budget and profile tests remain required.
These checks do not establish live legal quality.

The launcher is bind-mounted read-only; changing the file cannot update an existing
Python process. A reviewed release must recreate **both** Hermes profiles, preserve
their separate state/credentials and confirm complete large synthetic input in both
passes. No upstream image/source upgrade or automatic production restart is part of
this local change. Roll back via a compatible application reader and coordinated
profile recreation; do not claim the earlier65,536 behavior is safe for large inputs.

Source: exact pinned upstream `gateway/platforms/api_server.py`, normalization functions
and `MAX_NORMALIZED_TEXT_LENGTH`; no main-branch behavior assumed.
