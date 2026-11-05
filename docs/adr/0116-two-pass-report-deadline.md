# ADR 0116: Separate final-report wall from the unchanged model/intake caps

Date: 2026-11-05. Status: accepted as the requested release-scenario reliability fix.

Three ordinary synthetic analyses reached the shared 115-second case deadline.
The same configured high profiles completed an isolated two-pass diagnostic in
86.71 seconds (69.73 researcher, 16.95 reviewer), with seven validated proposed claims.
That diagnostic published no report and did not establish production readiness.
The case route also needs Core context retrieval, verification and PDF persistence.

The final-report route now has one shared 150-second wall for the entire pipeline.
Each model request retains its 115-second cap; intake/answer limits remain unchanged.
The worker HTTP operation timeout is 160 seconds and its total request wall is 165,
both below the existing 180-second fenced lease. No unbounded wait or fresh overall
budget per stage is introduced. Expiry still cancels in-flight streams; unverified
or late output is never submitted. Persisted routing receipts remain recoverable.

Models, high reasoning, provider, evidence selection, semantic review, risk policy,
permissions, subscription quotas and concurrency are unchanged. This permits longer
valid processing; it does not make the external model faster or guarantee availability.

Validation covers two successful sequential stages exceeding the former shared wall,
remaining-budget cancellation, stream closure, every context/reasoning/submission
deadline, V2 preparation and retained lawyer routing receipts. Production acceptance
still requires an ordinary queued case to reach a verified report and actual PDF.
