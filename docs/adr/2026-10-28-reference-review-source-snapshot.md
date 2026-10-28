# Bind reference approval to the material the reviewer saw

Date: 2026-10-28

## Problem

An old approval button sent only the case ID and decision. After changes were
requested and a contributor submitted another version, that old action could
approve the new unseen material. An idempotency key derived from a fresh lookup
does not prove what the human reviewed. These approvals can later make a reference
scenario eligible as a nonauthoritative analogy; normative approval is separate.

## Decision

Every newly produced reference detail includes `reviewSnapshotSha256`, a canonical
SHA-256 digest over source identity and reviewable content: clinic/case/version
IDs and numbers, creator identities, stored and actual-text scenario hashes,
attachment presence/hash/size/MIME, evaluation date, expected route and group.
Changing any of these invalidates an old displayed token. Storage object keys
are excluded: relocating the same exact bytes with identical hash/size/MIME is
content-equivalent. No raw text, bytes, object key or identity is exposed by the
opaque digest itself.

Workflow status, updated time and expiry are excluded: successful approval changes
READY to APPROVED and clears expiry without changing the reviewed source. Existing
live-content/expiry guards still run independently. A lost successful reply can
therefore retry the same body/key and recover its single existing approval event.

The additive request field `expectedReviewSnapshotSha256` accepts only a lowercase
64-character hexadecimal string. An absent field is omitted when serializing so
older nonapproval request hashes remain unchanged. Detail permits an absent value
only when reading retained legacy creation/revision receipts; fresh detail
producers always compute it. The client must retain the token from the displayed
detail rather than silently fetch a new token for an old button.

The server resolves the existing explicit reviewer grant, locks Case then its
current Version, rechecks existing actor/workspace access after those waits and
checks live content and independent authorship. It compares a supplied token to
the locked current source **before idempotency replay or any mutation**. A stale
source returns409 `REFERENCE_REVIEW_SOURCE_STALE`, instructing a fresh view.
It creates neither an approval event nor an approval audit/receipt on rejection.
The existing metadata-only audit includes expected/current snapshot digests when
provided; no new table, migration, role or permission is introduced.

APPROVE_FOR_EVALUATION requires a token even with the analogy feature disabled.
Otherwise a tokenless legacy approval could become AI-eligible when the flag was
later enabled. This is an intentional refresh requirement for old approval
clients. With `AI_FIRST_REFERENCE_ANALOGIES_ENABLED=1` every review decision needs
the token. With the flag off other legacy decisions may omit it; any supplied
token is always checked. Existing tenant, independent-reviewer, state, privacy,
expiry and authorization restrictions remain enforced. No production approval is
performed by development or migration.

All review, receipt and audit writes are explicitly flushed before final access
validation and commit, including idempotent replay. This lets actual foreign-key
waits finish before checking current active actor/role, reviewer grant and active
entitlement. Entitlement time predicates use `clock_timestamp()` rather than
transaction-start `now()`. The source's original expiry is captured before the
decision clears or extends it; final validation compares that captured deadline
with fresh wall time. A rejected late result rolls back the approval, expiry
change, receipt and audit together. No writes follow final validation except
commit, and no new authorization-lock ordering is introduced.

## Verification

Real PostgreSQL tests reproduce old-v1 approval after v2 becomes ready, same-version
attachment upload/replacement and metadata changes, successful approval lost-reply
replay, legacy nonapproval hash/replay, missing tokens under both flag values and
foreign/withdrawn/self/expired access. Strict request tests reject malformed hashes
and verify omitted-field compatibility. Tests use synthetic material only.
Actual FK-wait regressions revoke membership, user, reviewer grant or entitlement,
let an entitlement expire naturally, and let original source expiry elapse during
approval or changes-request persistence; all must roll back. Existing same-source
lost-reply replay remains successful.
