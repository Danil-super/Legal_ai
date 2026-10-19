# Main CI deadline headroom, 19 October 2026

PR100 head `de26891` passed all gates in run `39036329497`. Merge `ba40db4`
has the identical source tree `716e6f36c79a2f7b7f30761ca3d792508bc748ff`.
On main run `39269562211`, integration shard 2 (job `118117970831`)
started its test command at 14:37:12.188 UTC and exited 124 at 14:40:12.243.
The last displayed test passed at 75%; no pytest FAILED/ERROR markers occurred.
Other integration shards and quality/security/container checks passed. This is
a deadline termination, NOT a completed integration pass. Deployment stayed blocked.

Partition the same complete configured suite into six isolated matrix runners
instead of three. The selector, configured test roots, test filters (none),
PostgreSQL/MinIO enablement, fail-fast setting and success-only aggregate are
unchanged. Keep the 180-second outer process deadline, all application limits,
required security checks and deployment dependencies unchanged. No test is removed
or skipped, and no production application source or configuration is changed.

Regression contract first failed against the three-worker workflow (one failed,
17 passed). Add both three/six-worker real-checkout partition checks: every
canonical test file appears exactly once and all configured roots remain covered;
both selector exec variants preserve the complete unfiltered pytest command.
Independent review and fresh PR/main CI are still required before deployment.

Pre-release server remains on `b052f40` with eight healthy services. Root-only
custom database archive and readable TOC are retained under
`/var/backups/dental-legal-ai/pr100-20261019-1429`; no restore drill is claimed.
Ledger baseline: 54 approved norms, eight reference reviews; 67 reference cases
are DRAFT, not automatically approved or claimed active in model examples.
No migration, producer activation or live case smoke has run. Temporary desktop
worktrees have only been inventoried, not removed; four contain uncommitted work.
