# Recover narrative saves before starting case interpretation

The Telegram intake previously showed the same privacy warning for an invalid text
length, a Core rejection and an unknown write outcome. Two lost save responses could
also leave the user with that warning after Core committed the narrative.

Distinguish local input errors from Core/domain and transport outcomes. Keep the
original creation key for a resend in the same input session. Reconcile an uncertain
save by an authenticated read of the exact draft ID, expected revision, state and
payload. If that cannot prove success, show how to retry without creating a second
draft in the same session and link the exact draft when it is known. Do not write
through later confirmed answers. Do not send the description to interpretation
until its persistence is confirmed.

There are no changes to persisted schemas, redaction rules, role checks, risk
classification, or approval requirements. Tests inject lost acknowledgements and
definite rejections rather than assuming the root cause of a production screenshot.
