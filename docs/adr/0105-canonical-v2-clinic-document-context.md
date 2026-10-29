# ADR 0105: Select clinic document context from canonical v2 facts

29 October 2026. Clinic document upload and approval already produce tenant-owned,
versioned fragments. Their local retrieval feeds the researcher projection and
the canonical report's separate `clinicDocuments` section. However, the local
query and readiness planners still read only the legacy service, incident and
patient-demand fields. Current v2 cases therefore used generic queries even when
the confirmed case identified an implant service or a medical-records situation.

For `GUIDED_V2`, both planners now use the actual treatment service labels from
`AFFECTED_SERVICES`, scoped by the confirmed `TREATMENT` situation area. Readiness
checks all those service labels, not only the first. The records situation and
validated factual screening can select records, written-requirement or monetary
context. These are local retrieval candidates and non-blocking workflow hints;
they do not create diagnoses, classify a formal claim, establish a refund right,
or populate legacy facts. Stale legacy fields do not influence the v2 planner.
Legacy intake planning keeps its existing behavior.

The eight-query/twelve-fragment retrieval bounds are unchanged. The SQL view
still requires the latest explicit approval state, the current clinic scope and
the case's applicable date. Pending, blocked, retired, foreign or date-inapplicable
versions are not authorized by this change. Source hashes and the clinic-context
trace remain part of analysis freshness checks. No approval, upload category,
external model, risk policy or access right changes.

Clinic files remain internal context rather than legal authority. Only existing
bounded, pseudonymized retrieved excerpts reach the researcher; the independent
reviewer's normative evidence stays separate. Case attachments retain their
existing boundary and are not automatically converted into this library.

Validation includes v2 planner regressions and a real PostgreSQL/MinIO file
upload, explicit approval and date check followed by the report-free v2 receipt,
real Core/Agent HTTP clients, durable worker, researcher context, verified report
and PDF. External model HTTP responses alone are synthetic. Removing the v2
service selector makes that end-to-end scenario fail before a report is produced.
