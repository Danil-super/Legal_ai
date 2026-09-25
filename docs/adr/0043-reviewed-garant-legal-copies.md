# ADR-0043: Admit attorney-provided Garant copies as review-only legal evidence

- **Status:** Accepted
- **Date:** 2026-09-25

## Context

The product owner supplied a package of legal materials selected by a lawyer for the Dental Legal
AI corpus. Fifty-one RTF files identify `internet.garant.ru` as their source. The package must be
preserved intact and presented to a platform legal editor, but it is not an official publication
artifact.

ADR-0020 already established the `THIRD_PARTY_VERIFIED_COPY` path for a specifically authorised
commercial publisher. The product owner has now explicitly authorised Garant as an additional
trusted copy source. The existing schema admitted only `www.consultant.ru` and the editor could
only deliver PDF or plain-text artifacts, so a Garant RTF could not enter the same controlled
review workflow.

## Decision

Introduce `dental-legal-corpus.v4` for immutable Garant copies only. Its source key is `garant`;
both source URL and base URL must use HTTPS host `internet.garant.ru`, and its allowlist must be
exactly that single host. The manifest requires `VERIFIED_COPY`,
`THIRD_PARTY_VERIFIED_COPY`, full normalised text, checksums, retrieval time and an immutable
local artifact, exactly as the prior complete-copy workflow does.

RTF artifacts are accepted only when their bytes start with the RTF signature. The editor may
download an RTF as a document and sees it labelled **Garant — verified copy, not a primary
publication**. The approval preflight repeats the signature check.

Ingestion creates `REVIEW_REQUIRED` versions only. A `LEGAL_EDITOR` must compare every copy with
the official text and applicable edition, verify the full artifact, effective dates and selected
fragments, then explicitly approve it. Production retrieval remains limited to date-applicable
`APPROVED` versions. The package itself and the authorisation do not approve a document or permit
an automatic patient response.

## Alternatives considered

### Label the RTF files as official raw artifacts

Rejected: the raw bytes are Garant copies, so calling them official would falsify provenance.

### Convert RTF files to text and discard the originals

Rejected: a reviewer needs the original immutable artifact as well as normalised text; conversion
alone cannot establish the identity of the supplied copy.

### Allow arbitrary third-party hosts in a manifest

Rejected: it would let a future manifest silently expand the trusted-source boundary.

## Consequences

- The lawyer-provided Garant materials can be queued without pretending that they are official
  publications.
- Existing ConsultantPlus v3 manifests retain their narrower contract.
- A future publisher requires its own owner-approved ADR and an explicit code allowlist change.
- Materials that cannot identify a source URL or legal effective dates remain outside production
  retrieval until the editor obtains that evidence.
