# Package evidence matrix v1: operator contract

This is a private diagnostic artifact for the fixed package supplied for the
seven legal-editor groups. It is **not** a production corpus manifest, a source
verification, a legal approval, or proof that an edition is current.

## Input

`package-evidence.v1` JSON is an operator-maintained, UTF-8, owned regular file
with mode `0600` (at most 100 KB). It contains:

- `package_key`: exact immutable receipt package key;
- `originals`: exactly 58 distinct `{material_id, raw_sha256, kind, group_key,
  expected_part_keys}` records. Kinds: 50 `NORMATIVE`, seven
  `CLINICAL_REFERENCE`, one `REFERENCE_FORM`; the latter is in `healthcare`.
  The groups have 7 clinical, 7 labour, 10 courts, 4 privacy, 3 licensing,
  22 healthcare and 5 general originals. Normative keys are `part-1` onward;
  48 originals have one part, one general-law original two and one four, for
  54 intended parts. The expected keys are operator inventory, not parser proof.
- `legacy_version_ids`: exactly six distinct, explicitly supplied UUIDs for
  prior corpus versions. The generator never guesses their relationship to the
  incoming originals from a title, number or checksum.

No titles, extracted text, patient data, source files or credentials belong in
the input. Extra JSON fields fail validation. Exact receipt IDs, SHA and kind
must match the selected database package. A missing or extra receipt, changed
preparation kind/group/hash, unexpected part key or binding, mismatched binding
material/SHA, mismatched bound-version SHA, or missing legacy UUID fails
the entire operation. A missing preparation or missing metadata field appears
as a blocker; it is **not** filled from a filename or adoption date.

## Output and interpretation

The `package-evidence.v1` output contains exact original UUID/SHA/group/kind,
current preparation UUID/revision/digest, declared extraction scope and text
SHA, limitation count, source URL/locator and completeness locator, one row per
expected part, current exact P9 binding/version/status if present, reference
review event UUID if present, and six separate legacy-version rows.

Each part has `fields` for title, canonical key, document type, issuer, official
number, adoption date, publication date, version date, effective-from and
effective-to. Each field carries the **existing candidate value** and exact
preparation evidence locator, or `null` with an explicit status. Status
`CANDIDATE_WITH_LOCATOR` does not mean legally verified. Effective-to can be
`NOT_APPLICABLE`; all other listed fields require evidence for a ready
normative candidate. The matrix excludes raw artifacts, normalized document
text, OCR text, receipt titles, notes and limitation prose. It never asks an
LLM to supply a field.

`input_sha256` binds the inventory JSON and `snapshot_sha256` detects accidental
changes to the output. Neither digest authenticates the operator or proves
legal accuracy. The database read uses a single PostgreSQL repeatable-read,
read-only transaction. No approval, source transition, association, reference
review or database write is performed.

The output is a private file (mode `0600`) created exclusively in an already
owned, private directory; it is never overwritten. CLI success is silent and
failure text is generic so candidate data cannot spill to terminal logs.
Do not put the file in git, CI artifacts, chat, or ordinary logs. Operators
must inspect candidate fields for patient data before sharing the file with an
authorized lawyer.

```bash
python -m legal_core.package_evidence_matrix generate /private/inventory.json \
  --output /private/evidence.json
python -m legal_core.package_evidence_matrix validate \
  /private/inventory.json /private/evidence.json
```

Validation verifies shape, known blocker/status codes, exact inventory
correspondence and snapshot digest.
It does not re-query the database. Re-running `generate` requires a new output
path; comparison of two snapshots is an operator review step, not an approval.

## Gates after the matrix

The 54 parts still need evidence-backed canonical identity, publication and
edition/effective dates, complete extraction and exact P9 bindings. The two
code bundles must account for all four and both parts. The eight references
receive separate human review, not normative approval. Six older versions
remain visibly separate until an explicitly reviewed reconciliation. Only the
authenticated lawyer group-preview/approval flow may approve ready normative
versions; this matrix cannot make any group complete by itself.
