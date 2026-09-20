# Security CI follow-up, 2026-09-20

Prepared fixes were published to main in 310880d. Its first full run passed Bandit
and all four production-image import jobs. Gitleaks reported four generic-api-key
false positives; source inspection did not reveal credentials in those four lines.

For the three Pydantic declarations, the generic rule treats the comma after the
requiredFactKeys alias as an assignment separator and captures the max_length bound,
not the alias itself. The exception therefore targets that complete, exact matched
expression AND the three exact contract paths. The fourth exception targets only the
exact replacement placeholder AND production.env.example. All default rules remain
active. Sensitivity controls add never-issued random values to the same four paths;
exceptions must not stop their detection. No generated or production values are logged.

The next source revision additionally adds kernel resource bounds to native clinic
PDF tools and tests encrypted-PDF metadata, including an empty user password. The
Core-image test runs real Poppler under the production non-root user with no network,
a read-only root and no capabilities. No production data, model call or credential
is used. No claim is made about an independent filesystem sandbox, historical Git
secrets, container OS vulnerabilities or live legal-answer accuracy.

Run 35522348127 passed the native Core-image tests and all application unit tests
(687 passed, 75 skipped in the unit-only job), but remained blocked by two line-length
lint errors and the first, incorrect alias-only Gitleaks exception. Both controls
were corrected, not disabled. The succeeding CI revision is the authoritative record
of final checks and deployment; a local passing subset is not a full CI result.
