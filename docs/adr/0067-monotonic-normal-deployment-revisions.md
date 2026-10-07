# ADR-0067: Prevent delayed normal deployments from replacing newer revisions

## Status

Accepted

## Context

The restricted deployer validates a requested commit against `origin/main`, but a
successful older CI run can arrive after a newer revision has already deployed.
The deployment lock prevents simultaneous execution, not reversed queue order.
Merely accepting every ancestor of `origin/main` permits an unintended rollback.

The root-owned deployer is installed separately from the application checkout.
A merged change to `ops/deploy/deploy-commit.sh` does not update its installed copy.
Reapplying the same SHA must remain possible when the operator changes the
persistent analysis profile or other environment settings.

## Decision

After origin validation, fetch and requested-revision validation, but before
checkout, configuration, build or container replacement, normal `deploy` compares
the request with `last-successful-revision` while holding the existing lock:

| State/request relationship | Normal `deploy` |
|---|---|
| No state file | Bootstrap remains allowed |
| Same SHA | Redeploy remains allowed |
| Successful SHA is an ancestor of requested SHA | Deploy remains allowed |
| Requested SHA is a strict ancestor of successful SHA | Safe no-op, exit 0 |
| Neither SHA is an ancestor of the other | Fail closed, exit 65 |
| Invalid state or state SHA is not a locally available commit | Fail closed, exit 65 |

State must be a readable regular file, not a symlink, with exactly 40 lowercase
hexadecimal bytes and optionally one LF. Its size and a bounded read are checked;
NUL bytes, extra lines, missing objects and non-commit Git objects are rejected.
Errors report an operator action without echoing state contents.

Explicit `rollback` retains its previous semantics, including recovery from
malformed state. It still passes the shared origin and `origin/main` ancestry
validation. This guard does not introduce a new operation, credential, environment
setting, implicit rollback or automatic database downgrade.

## Consequences

- A delayed successful CI run for an old revision cannot replace a newer successful
  stack, change the checkout or overwrite success state. A stale no-op is successful
  because the later revision has already satisfied deployment readiness.
- Same-SHA deployments still rebuild and apply the selected persistent profile;
  the guard does not suppress an operator's environment rollout.
- Divergent commits can both be reachable from a merge on `main` yet remain
  incomparable with each other. An operator must investigate rather than allowing
  queue order to determine the installed branch.
- Missing state remains the existing bootstrap behavior; deleting state is not a
  rollback mechanism. A corrupt or missing Git history requires operator review.
- Rollback and migration compatibility remain separate operator responsibilities.
- Tests run the actual post-lock shell body against real local Git repositories,
  with only Docker/syslog side effects faked. They use no server or external fetch.
- The root-owned installed deployer must be updated explicitly from a reviewed,
  CI-green immutable checkout; ordinary application deployment does not install it.
