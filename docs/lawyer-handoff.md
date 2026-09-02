# Safe lawyer handoff

When a server-verified analysis has `HIGH` or `CRITICAL` risk, the Telegram bot emits a second,
copyable internal packet for a clinic administrator to send to its lawyer manually. It includes only
the public case number, risk level/reason codes, approved legal source metadata and the existing
non-binding clinic-document checklist.

The packet deliberately excludes free-text case description, recommendations, patient-response
draft, names, contacts and the contents of clinic documents. It does not notify a lawyer
automatically and does not grant a lawyer access to a clinic; the administrator chooses whether to
send it through an agreed protected channel. Any lawyer account, clinic mapping, protected document
exchange, comments or a formal decision record require a separate access-control and personal-data
design.
