# Clinical menu instructions match the released workflow

Date: 2026-11-17

The owner requested a complete functional/button audit. Public clinical menus
still described the bot as a demonstration, described future functionality,
and said files were not accepted. The active clinical composition supports
narrative intake, missing-fact questions, final fact confirmation, PDF reports,
history, and explicitly requested deidentified TXT/PDF/DOCX attachments up to
15,000,000 bytes. Existing source and authorization checks remain authoritative.

Correct only those instructions. Preserve patient-data restrictions, internal
use of recommendations, lawyer review, approved-source requirements and the
separate hidden personal-preview boundary. This decision changes no route,
role, grant, model, approval, risk policy, retention rule or startup flag.

The independent `buttons_boundary_review` inspected the clinical diff and
confirmed that updating only the frozen `ui.py` Git blob in
`test_personal_clinic_boundary.py` is permitted by that test's separate-review
rule. Keep the assertion and every other frozen file. The previous full-unit
run failed this assertion, demonstrating that it still detects UI changes.

Add 64 dispatcher regression cases using the existing synthetic boundary
harnesses. They cover shared owner/admin/lawyer controls and return navigation,
confirmed draft deletion, clinic-library navigation, all seven approved groups,
and selection/removal of all 22 checklist choices without leaving the card.
These tests do not claim real Telegram transport or full legal certification.
