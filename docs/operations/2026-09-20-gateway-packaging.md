# Telegram packaging regression — 2026-09-20

The a6f4de4 CI quality and PostgreSQL/MinIO jobs passed. Production deployment run
35510593678 failed after recreating containers because telegram-gateway was unhealthy.
The deployment log did not include the gateway traceback, so it does not by itself
identify every possible production startup failure.

Code inspection identified a reproducible packaging defect: legal_conclusion_display
imports legal_core.contracts, but the gateway Dockerfile copied only __init__.py and
pseudonymization.py from Legal Core. Importing the composed entry point in that image
therefore cannot succeed. Source-tree tests masked the missing file.

The fix copies the dependency explicitly and imports the composed gateway at image
build time as the runtime user. It does not copy the database/API implementation into
the gateway, alter credentials, enable Hermes, approve evidence or alter risk policy.
Dependency installation precedes source copying so code-only changes can reuse its cache.

Regression tests reproduce the exact Docker COPY file set in an isolated temporary
runtime, disable editable-install hooks, and import the real entry point. A negative
control removes contracts.py and requires the original ModuleNotFoundError.

After deployment, confirm the gateway is healthy and exercise a synthetic case. A
successful container import proves packaging closure, not Telegram connectivity,
provider readiness, approved legal evidence or the correctness of legal conclusions.
