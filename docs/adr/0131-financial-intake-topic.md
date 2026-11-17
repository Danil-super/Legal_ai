# Financial questions use the existing factual topic contract

Status: implementation, 2026-11-17.

The lawyer could not identify a topic for payment, prices, additional charges and
refunds. Add FINANCE to the existing situationAreas choices, displayed as
«Оплата, стоимость и возврат денег». The six topic values remain unique and
bounded. Existing drafts and the v2 wire shape remain readable; no storage,
identity, risk-policy or legal-approval change is required.

DraftSuggestions is the shared strict proposal reader for both initial text and
follow-up interpretation. Extend that reader before exposing FINANCE in the
gateway or model instructions. Keep source spans, factual confirmation and the
existing final-summary consent. A selected topic is not a legal finding.

FINANCE alone must not create medical service slots or health consequences.
TREATMENT combined with FINANCE still requires the actual affected services.
Existing monetary/safety facts and risk v4 determine routing independently.
Retrieval may search for applicable payment rules, but only approved, date-bound
fragments can support a recommendation. Neither a topic nor a retrieved example
establishes entitlement to a refund or proves informed consent to extra services.

Regression checks cover grounded proposals, all six topics together, the menu,
fact serialization, and the absence/presence of service questions. Follow-up and
initial model contracts must accept the same choice; existing illegal-enum,
duplicate-choice, source and authorization checks remain required.
