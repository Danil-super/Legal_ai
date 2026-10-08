# Lawyer-supplied corpus: readiness audit, 2026-10-08

## Verified state

The owner attests that the supplied documents were provided by a lawyer and are
current. This attestation is accepted as package provenance; it is not an
automatic LegalVersion approval, nor an assertion of missing edition dates.

- Local original count: 58, total 69,305,904 bytes; 116 original/text hash checks
  passed against the private `technical-v2` preparation package.
- Classification: 50 normative RTF originals (54 declared parts), seven clinical
  PDF references, one blank form. All original bytes remain unchanged.
- Authenticated read-only production checks succeeded for all seven groups.
  Ready: six previously prepared normative versions and eight reference originals.
  The 50 new normative originals remain unbound. Approved library count: zero.
- Focused preparation/operator/reference tests: 40 passed, five skipped because
  disposable PostgreSQL was not configured. This is not an integration approval.
- No production writes, human approvals, Telegram messages, or model calls were
  performed by this audit. Code baseline: `7193d95895346cce20caa7fac95dba723e3ea035`.

## Findings from the supplied copies

Neither publication dates nor dates identifying the supplied consolidated
editions were found for the 54 normative parts. Initial commencement dates in
some acts and dates of cited amendments do not establish these edition dates.
One Supreme Court overview is genuinely unnumbered. Twenty-two issuer fields
are absent from the current cards; one overview issuer is explicit in its title.
President signatures do not on their own resolve the remaining issuer contract.

Eight RTF originals contain 208 graphic blocks. Some tax-code formula expressions
are absent from the extracted text: a formula introduction is followed only by
punctuation or the explanation of its variables. This is an observed extraction
loss, not just a theoretical layout concern. Do not promote these artifacts from
PARTIAL to FULL_DOCUMENT or assert that every graphic was lost.

## Accepted path — production release pending

Avoid demanding a new field-by-field legal investigation of every document.
The owner accepted a distinct human-reviewed current-copy contract on 2026-10-08.
The following contract is implemented and tested locally; production is pending:

1. Keep all originals and seven groups; keep normative approval separate from
   clinical/form review. Do not classify references as laws.
2. Preserve absent publication/edition dates as unknown. Record human review
   time and applicability-on-review-date separately, never as statutory dates.
3. Bind the lawyer's explicit group confirmation to the exact originals, prepared
   text, selected usable fragments and membership. Retain atomicity, replay,
   stale-snapshot rejection and existing LEGAL_EDITOR authorization.
4. Do not silently apply the current copy to earlier events. Unknown historical
   applicability requires a dated edition or escalation, not a model inference.
5. Preserve extraction limitations and downloadable originals. Omitted graphic
   formulas must not become evidence through empty or misleading fragments.
   Resolve required graphic content, or exclude affected passages and fail closed
   when a case needs them. This is not FULL_DOCUMENT normalization.
6. Implement an additive, versioned contract with migration, ADR, synthetic tests
   and real disposable-PostgreSQL approval-to-retrieval tests before release.
   Existing dated-version and approval guards must retain their behavior.

This proposal must not be enabled using metadata-only edits, fabricated dates,
direct status updates or automated attestations. Production changes remain
pending successful release and preparation. The verified state above is the
read-only production baseline, not a claim that this implementation is deployed.

## Local verification after acceptance

- Fresh PostgreSQL run: 841 passed, one external MinIO integration skipped;
  includes migration, downgrade, dump/restore and approval-to-retrieval guards.
- Private preparation: 50 normative originals, 54 parts, 17,119 usable fragments;
  330 graphic/formula-dependent blocks excluded. No actual documents approved.
- Exact originals and all seven existing groups are preserved. Eight references
  remain a separate review action, not legal evidence.
