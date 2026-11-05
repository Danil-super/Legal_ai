# ADR 0117: Align the pinned high profile's inner provider wait

Date: 2026-11-05. Status: accepted within the requested production scenario repair.

The release scenario still encountered per-profile WALL_TIMEOUT after the complete
case-route budget was corrected. The pinned Hermes profile explicitly used 55-second
provider operation/stale settings. The pinned SDK applies those values to upstream
requests and its non-stream stale detector; large high-reasoning requests can need
more time before a complete response. An isolated valid research pass took 69.73 s.

Set both configured provider waits to 90 s, below the existing 115-second model caller
wall. Preserve api_max_retries=1: in pinned Hermes 0.20.6 this means a single attempt,
not one extra retry. Intake callers retain their shorter 65-second wall and stream
cancellation; the final pipeline retains one shared 150-second wall, worker165 and
fenced lease180. No infinite waits, additional attempts, model/provider/credential
changes, lower reasoning, tool access, source changes or legal-policy changes.

The startup guard requires the new exact resolved values. Compose labels bind both
profiles to the profile and guard hashes, so updating bind-mounted files recreates
the processes and reruns the native preflight. Existing launcher hashes remain pinned.
Tests reject old/missing/unbounded/mismatched values and extra attempts; native pinned
SDK preflight must pass before rollout. Only an ordinary queued verified report/PDF
may close production acceptance; longer waiting alone does not establish readiness.
