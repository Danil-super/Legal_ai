# Distinguish the full scoped court institution from an unlabelled person name

The live expanded retrieval included a public approved document titled
`Постановление Пленума Верховного Суда РФ ...`. The shared conservative three-word
name detector classified the first three capitalized words as a person and refused
the bounded projection before any model call. This was a lexical false positive,
not provider unavailability or missing legal approval.

Both redaction and the final identifier guard now use the same unlabelled-name
matcher. It excludes a name-shaped match only when its complete character span
lies inside the exact whole `Пленум/Пленума Верховного Суда РФ/Российской Федерации`
institution span, optionally prefixed by `Постановление`. Word boundaries prevent
partial country designations. Institution words are not globally exempted; no
legal metadata field, evidence text, document or title bypasses the scanner.

All other identifier patterns remain independent. Patient-labelled names and
explicit known identifiers take precedence even when their spelling equals the
institution. Adjacent names, emails, phones and initials remain detectable and
redacted. An unscoped institution-like phrase remains conservatively flagged.
No privacy category, source, schema, approval, provider or model changes.

Tests reproduce the failure with the actual bounded public title/issuer and point52
text through LawyerDraftGenerator and real HermesClient MockTransport. After the
fix both profile calls receive the intact title; an adjacent synthetic person name
still prevents the first call. This is transport/privacy proof, not semantic legal
quality or proof that a synthetic model claim is supported by that public fragment.

Independent review found quadratic containment when many public titles recur in
raw DOCX XML before its normalized-text limit. Containment now merges the ordered,
non-overlapping name/institution matches with a monotonic institution iterator.
Each institution is visited at most once; full-span semantics are unchanged.
Regression tests count actual offset comparisons for repeated institutions mixed
with private names, covering both the guard and redaction without a wall-clock
threshold. The original nested scan fails these tests; the linear scan still
detects/redacts every adjacent synthetic private name.

The existing patient-label alternatives use inline case-insensitivity restricted
to the label itself. Thus ПАЦИЕНТ/ПаЦиЕнТ and mixed-case ФИО retain precedence over
the public institution exemption. Capitalization rules for name words and the
exact institution whitelist remain unchanged. Real generator regressions reject
these identifiers in model actions before the semantic-reviewer request.
