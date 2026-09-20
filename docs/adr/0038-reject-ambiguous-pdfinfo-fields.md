# ADR 0038: Reject display-metadata spoofing of PDF security fields

Date: 2026-09-20
Status: Accepted

## Finding and decision

A further synthetic check reproduced a bypass of the newly tightened PDF preflight:
Poppler displays PDF-controlled Title, Subject, Author and Keywords literally,
including embedded newlines. A value can therefore add a fake Pages or Encrypted
line before the tool's actual security fields. A first-match regex can accept a
blank-user-password encrypted PDF or undercount pages. This is an application
preflight bypass, not a claim that PDF encryption was broken or that production
was compromised. Resource bounds still apply independently.

Require exactly one complete Pages field and one complete Encrypted field before
extracting text. Reject duplicate fields even if one value is malformed. Do not
select the first/last match. Restrict whitespace to a single physical line; accept
only bounded ASCII decimal page counts and the exact Encrypted: no value. Missing,
wrapped, duplicate and uncertain fields fail closed. Ambiguous display metadata in
an otherwise benign document can be rejected; there is no unsafe fallback.

This tightens acceptance during the same PDF v2 rollout without changing accepted
text extraction, hashes, stored versions, legal approval, risk or model behavior.

## Regression evidence

Eight actual small synthetic PDFs cover two spoofed fields in four metadata
locations. The page-count tests set a one-page limit and use two pages, avoiding
large adversarial files. Encryption uses an empty user password and a synthetic
owner password. Five mocked outputs cover duplicates, malformed duplicate values,
untrusted suffixes and wrapped lines. All thirteen tests failed against fc77e249's
first-match logic. After the fix, all 32 native/parser tests pass locally. The same
test file is mounted read-only into Core-image CI and executed without network or
capabilities as the production non-root user. Final remote checks remain tied to
the committed revision; no production file or credential is used by these tests.
