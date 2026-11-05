# ADR 0118 — Legacy service-dependent legal assessments keep their date boundary

Date: 2026-11-05. Status: accepted; authorized audit remediation.

The audit demonstrated that a legacy LEGAL claim explicitly requiring SERVICE_DATE
could be marked VERIFIED using evidence that became applicable only after that
service. GUIDED_V2 already checks its bound service dates; legacy claims lacked
this additional structural check when the case date came from the later complaint.

For legacy LEGAL claims explicitly depending on SERVICE_DATE, require an exact,
valid date and require every cited fragment to be effective on that date, in
addition to the existing case-date and independent semantic checks. Unknown,
approximate or invalid dates fail closed. No metadata or historical edition dates
are inferred. APPROVED copies are not backdated.

Current organizational ACTIONs keep the case-date basis; requiring the service
date as a fact does not automatically make a present invitation or document check
a retrospective legal assessment. GUIDED_V2 rules, risk policy, source approval,
wire schemas and old results are unchanged. The semantic reviewer must still
identify missing dependencies and reject unsupported temporal assertions.

Regression tests distinguish historical/legal claims, invalid/approximate dates,
the inclusive effective-from boundary, and current organizational actions.
