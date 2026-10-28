# ADR0101: One model call for a factual answer and source-bound additions

27 October 2026, implementing approved AIF03/04. The original single-question
contract remains supported. A new optional `factualProposal` is omitted when absent,
preserving legacy serialized responses and proposal hashes.

The Agent may use the rich path only when Core supplies retained source segments
and an allowed target list. The currently supported source bundle contains the
current retained narrative plus the latest answer; previously staged values stay
in typed factual context. Uploaded text/files, actor IDs and tool permissions are
not added to model context. This does not claim recovery of all historic raw replies.

The same existing researcher/profile makes one call. The numeric-only monetary
shortcut still makes none. The current question can be corrected; additional
targets are fill-only. Existing UNKNOWN/negative values cannot be silently replaced.
Candidates reference exact source IDs, hashes and offsets, use existing closed
field/value contracts and retain date/service identities. Additional money needs
literal ruble currency and exact conversion. Related fields are validated as one
atomic batch, irrespective of candidate order.

Core independently repeats target/source/value checks before staging, under its
existing ownership, live lease, revision and audit fences. Additional facts remain
provisional until the final factual-summary confirmation. These structural and
literal checks are not proof of semantic truth; ambiguous or conflicting input
still requires a targeted clarification, never an inferred safe answer.

Core captures the source bundle and allowed target identities in the existing
owned answer-operation record only when `AI_FIRST_FACTUAL_INTAKE_ENABLED=true`.
It defaults to false for compatible-reader rollout. Fresh context reads and
completions compare that snapshot to the current draft; historical staging replay
still checks the existing proposal hash and immutable audit receipt. The private
context gains optional retainedSources/allowedQuestions fields, omitted for old
operations; public answer receipts never contain retained raw text or lease keys.
Agent dispatch uses the rich path only for a paired source/target bundle. Core
refuses additional candidates on an operation reserved without that bundle.
Both provisional staging and the legacy explicit-acceptance operation apply the
same atomic batch. Neither creates Case/CaseFact/Report records before final consent.

Independent review reproduced an older dependency bug: filling previously absent
incomingKind/healthSignals erased an already extracted safetyScreening object.
First fills now preserve these independent attempts. Replacing an existing value
retains the previous dependency invalidation; Core contradiction/risk gates are
unchanged. Regression tests cover both first fill and actual replacement.

The minimal Gateway image copies the two newly imported pure shared modules,
case_material_contracts and intake_factual_candidates. Its packaging canaries still
reject missing dependencies and prohibit importing ORM/API modules into Gateway.
The clinical Compose baseline is refreshed only for the owner-approved opt-in
flag; personal-preview isolation and its closed default entry point remain frozen.

For a SERVICES primary reply, Core supplies extraction templates for up to five
proposed service slots (date/payment). They carry scopeBasis=PROPOSED_SERVICES and
the primary input-scope digest. The model copies that server metadata; it never
computes a new scope hash. Core validates literal primary service names first,
then binds each supported candidate slot to that proposed list and exactly one
complete source quote that also contains its own service label. Separate date/name
anchors or separate sources cannot be stitched into a conditional service fact.
Out-of-range slots, old scope identities and
replacement of existing scoped attempts (including UNKNOWN) fail atomically.
Other current-scope questions keep their previous identity; absent scopeBasis is
omitted so legacy question hashes do not change. These are extraction permissions,
not an instruction to ask ten questions or assume those services exist.
The candidate count rises from twelve to twenty-four because five service date/payment
pairs plus other explicit facts can exceed twelve. The same 16000-character payload
budget, source limits, unique identities and Core-owned target list remain enforced.
A complete five-service reply with twenty-three explicit fields is regression-tested
as one atomic batch; increasing the count does not waive ambiguity or evidence gates.

A typed source UUID may contain a digit substring resembling a patient identifier.
The Agent scans factual values for direct identifiers, not validated UUID/hash/enum
metadata. Raw sources and context are still independently checked before model I/O.
A deterministic UUID/SNILS false-positive regression covers this distinction.
An independent model-boundary test also found that a sentence-final dot prevented
the existing email matcher from detecting/redacting an address. The shared matcher
now distinguishes terminal punctuation from a continued domain. Punctuation and
complete-domain regressions cover both detection and redaction; non-email metadata
is retained. This strengthens existing context minimization without claiming full
anonymization or introducing another provider/data category.

An unresolved current answer currently remains the existing recovery flow; it may
not hide additional candidates in a successful-looking result. No new normative
approval, risk/escalation rule, model/provider/profile or patient-send path is added.
This increment does not implement the lawyer work-product table/producer or legal
plan generation. Reader compatibility, PostgreSQL staging/authz tests and independent
review are required before release; local mock-model tests do not prove live speed
or legal quality.
