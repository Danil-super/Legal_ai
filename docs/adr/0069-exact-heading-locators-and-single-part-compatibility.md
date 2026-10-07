# ADR 0069: exact heading provenance and legacy single-part compatibility

Status: accepted for technical preparation; no legal approval or trust expansion.
Date: 2026-10-07.

## Context

The 50 supplied normative originals contain 54 intended parts: 48 single acts,
four Civil Code parts and two Tax Code parts. Candidate enrichment deliberately
preserved historical single-act `document` keys (ADR 0065), but the binding and
single-part operator compared those keys literally with parser key `part-1`.
Existing source URLs also retained generic candidate descriptions rather than
the exact parser-observed heading byte locator. Neither mismatch is a reason to
rewrite immutable receipts or pretend an extraction is legally verified.

## Decision

A shared compatibility check accepts `document` versus `part-1` only for one
normative, `FULL_DOCUMENT` part whose stored and parsed span is exactly
`0:len(normalized_text)`, with matching part, whole-normalized and original
checksums. It does not alias arbitrary keys or any multipart material. The
operator and binder retain all existing identity, source, date, fragment,
latest-revision and LEGAL_EDITOR checks. Requests, preparations and bindings
continue to use the persisted key. Existing hashes and replay identities do not
change; the PostgreSQL guard already validates that same stored key and span.

Candidate parser version `rtf-heading-candidates.v2` appends a new preparation
revision with the exact observed `RTF first centered title paragraph bytes X:Y`
source locator. The raw receipt hash binds its byte offsets. Only a matched
centered heading may supply this locator; conflicting URLs are rejected and a
body cross-reference does not establish a source. Repeating enrichment is
idempotent and old revisions remain intact.

The private generator also writes `candidate-scopes.json`, version
`dental-preparation-scopes.candidates.v1`, before its import manifest. It records
raw/normalized checksums, persisted and parser part identifiers, source byte
locator, Unicode-codepoint text offsets, scoped hashes and explicit candidate
blockers. The sidecar is not an importable approval artifact or completeness
attestation. Importable cards stay `PARTIAL`, with no persisted verified part
scopes, unchanged text, limitations and unknown edition/applicability metadata.
Clinical references and the reference form stay unchanged. No model, fetch,
database approval, new source or fabricated legal number is introduced.

## Verification and remaining boundary

Synthetic tests cover preserved legacy keys/digests, operator and real database
binding/replay, rejection of arbitrary and multipart aliases, changed scopes and
checksums, append-only exact locators, unknown dates and private sidecar output.
The ordinary preparation validation still rejects scoped `PARTIAL` cards.

The unnumbered court overview remains missing-number/unbound: this change does
not relax the corpus or database number requirements. Publication/edition dates,
effective intervals, canonical identity, table/graphic completeness and exact
fragments still need evidenced preparation and human review before a normative
version can be bound and approved. A technically located span is not a verified
applicable law.
