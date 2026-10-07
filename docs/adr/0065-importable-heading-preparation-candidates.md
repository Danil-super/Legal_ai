# ADR 0065: importable heading candidates for supplied legal materials

Status: accepted for technical preparation; legal approval remains a human action.
Date: 2026-10-07.

The 58 original receipts already have immutable preparation revisions, but observed
RTF source headings and identity candidates were only described in diagnostics.
This left their actual editor cards empty. The operator generator now produces a
package accepted by the existing atomic `preparation_import`, accounting for every
receipt filename and exact original SHA-256. Importing appends revisions rather
than rewriting original receipts or approving legal versions.

The generator reads current private preparations plus their immutable local
originals. For the 50 normative RTFs it copies observed source-heading URLs,
document types, named issuers, official numbers and adoption/signature dates into
preparation fields with candidate evidence locators. It preserves historical
single-act `document` keys and existing bundle `part-1` through `part-4` keys.
Letter suffixes such as `1051н` belong to the official number and must survive
extraction. Number and date observations do not establish canonical corpus identity.

All seven clinical references and the 043/у reference form keep their existing
preparation metadata and text. Normative extraction remains `PARTIAL`; candidate
part boundaries are not stored as verified full-document scopes. Canonical keys,
publication dates, edition dates, effective dates, and missing issuers remain
unset. Existing conflicting identity values fail the whole generation. The
generator never fetches sources, uses a model, writes to a database, binds corpus
versions, performs reference attestations, or grants legal approval.

Output is a new operator-private directory (`0700`) containing exclusive `0600`
files; metadata and large extracted text remain separate. Every input is validated
before creating output, original symlinks are refused, and the import manifest is
written last. CLI failures disclose neither document contents nor validation
payloads. Generated artifacts are not committed to git.

Synthetic tests cover real number suffix preservation, unchanged checksums and
references, package import validation, all-receipt accounting, conflict rejection,
code-part preservation, private output modes and symlink rejection. Real supplied
copies are checked separately and reported as observed candidates, never as
verified applicable law. The next legal-preparation step is human evidence for
the edition/applicability and completeness of each intended part; neither a
diagnostic nor this candidate import satisfies that requirement.
