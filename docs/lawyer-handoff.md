# Safe lawyer handoff

When a server-verified analysis has `HIGH` or `CRITICAL` risk, the Telegram bot emits a second,
copyable internal packet for a clinic administrator to send to its lawyer manually. It includes only
the public case number, risk level/reason codes, approved legal source metadata and the existing
non-binding clinic-document checklist.

The packet deliberately excludes free-text case description, recommendations, patient-response
draft, names, contacts and the contents of clinic documents. It does not notify a lawyer
automatically and does not grant a lawyer access to a clinic.

For an already mapped clinic lawyer, the bot also has a tenant-scoped critical-case queue and an
internal discussion thread. It contains only a public case number, risk metadata and bounded,
de-identified text entered by clinic staff or the lawyer. It rejects obvious identifiers and does
not accept patient files, medical records, contacts, personal Telegram links or automatic external
delivery. Protected exchange of source medical documents and a formal legal-decision record remain
outside this MVP flow.
