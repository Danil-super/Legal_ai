# Initial intake money is checked against its literal ruble source

Status: accepted bug fix within the existing factual-intake contract.

A production synthetic description requested 5,000 RUB, while the initial model proposal
contained `amountKopecks: 5000`. Initial proposal validation checked only anchor bounds;
subsequent answer validation already checked monetary units. The mismatch could change
risk routing after the final factual confirmation.

Keep the current requested-money type and risk policy. A shared pure local parser retains
existing answer behavior. For an initial description, it finds complete explicitly RUB
numeric mentions, requires one consistent amount across the source and a complete matching
safety anchor, and rejects another currency or a negated/uncertain monetary sentence.
Dates do not become money; negative health facts in separate sentences do not negate a
positive monetary request. Model-controlled spans cannot crop a larger numeric token or
hide a conflicting amount elsewhere in the input.

The Agent may correct only a factor-100 unit omission demonstrated by that literal source.
It keeps a correct amount, and omits an unsupported numeric amount while preserving the
other valid suggestions. Omission leaves the existing amount question unanswered; it does
not invent UNKNOWN or confirm any fact. Core independently rejects every numeric mismatch
at completion, retained-result replay and final merge. Raw inputs and errors are not logged.
This bound does not prove semantic truth or replace the final factual confirmation.

No model/provider, role, source approval, risk threshold, storage schema or patient-delivery
change is introduced. Telegram packaging includes the shared pure parser. Regression tests
cover the 5,000/10,000 RUB unit fault, full-input ambiguity and cropped anchors, currencies,
negative context, separate health anchors, unchanged subsequent-answer behavior and packaging.
