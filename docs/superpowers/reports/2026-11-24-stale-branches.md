# Stale Branch Report

Generated 2026-11-24 against `origin/main` at 98fe2eb. No branches were deleted. Deleting needs the owner's approval.

## Unmerged work worth keeping

Two branches are not behind `main` at all and hold real, unreviewed work:

- `fix/live-trading-safety-audit` (2026-06-08, 19 commits, 39 files): MFA-backed live session elevation, CSRF validation, HttpOnly cookie sessions, a persistent live circuit breaker, rate limiting and idempotency, content security policy headers, alert webhooks. It edits `ci.yml`, `frontend-ci.yml` and `python-package.yml`; Milestone 1 deletes the last two, so merging it will conflict on those files.
- `phase-21-audit-readiness-plan` (2026-06-17, 9 commits, 9 files): an event ledger, market-data freshness guard, live risk policy and idempotency guard, each with tests. No overlap with Milestone 1.

These overlap with Milestone 2 (accounts and keys). Decide whether to merge them before Milestone 2 starts.

## Merged into main, safe to delete

| Branch | Last commit |
|---|---|
| `phase-1-market-data-foundation-v2` | 2026-05-02 |
| `phase-2-backtesting-engine` | 2026-05-02 |
| `phase-20-event-ledger-live-hardening` | 2026-06-04 |
| `phase-3-paper-execution-engine` | 2026-05-02 |
| `phase-4-ledger-reconciliation` | 2026-05-02 |
| `phase-5-live-readonly` | 2026-05-02 |
| `phase-6-gated-live-execution` | 2026-05-02 |
| `phase-7-integration-hardening` | 2026-05-04 |
| `phase4-6-wire-lifecycle` | 2026-05-08 |
| `resolve-issue-39` | 2026-05-08 |

## Has commits not in main, review first

| Branch | Last commit | Commits ahead of main | Commits behind main |
|---|---|---|---|
| `architecture-map` | 2026-05-04 | 1 | 44 |
| `bugfix-runtime-stability-pass` | 2026-05-04 | 6 | 45 |
| `ci/baseline-ruff-stabilization` | 2026-05-28 | 7 | 6 |
| `emergency-production-hardening-audit-pass` | 2026-05-22 | 7 | 5 |
| `fix-codescan-env-and-secrets` | 2026-05-02 | 1 | 88 |
| `fix-codescan-java-17` | 2026-05-02 | 1 | 89 |
| `fix/live-trading-safety-audit` | 2026-06-08 | 19 | 0 |
| `fix/runtime-import-integrity` | 2026-05-27 | 9 | 6 |
| `hardening-engine-fixes` | 2026-03-25 | 4 | 109 |
| `omega-ci-complete-gate` | 2026-05-05 | 5 | 38 |
| `omega-e2e-paper-smoke` | 2026-05-05 | 2 | 37 |
| `omega-enforce-live-ops-scopes` | 2026-05-06 | 4 | 34 |
| `omega-live-safety-a` | 2026-05-06 | 10 | 33 |
| `omega-live-safety-a-complete` | 2026-05-06 | 9 | 32 |
| `omega-p1-platform-baseline` | 2026-05-04 | 13 | 41 |
| `omega-request-errors` | 2026-05-06 | 6 | 36 |
| `omega-scoped-authorization` | 2026-05-06 | 4 | 35 |
| `omega-typed-settings-report` | 2026-05-04 | 7 | 39 |
| `omega-worker-runtime-roles` | 2026-05-04 | 9 | 40 |
| `p0-live-safety-hardening` | 2026-05-04 | 8 | 52 |
| `phase-1-market-data-foundation` | 2026-05-02 | 2 | 89 |
| `phase-1-remediation` | 2026-05-02 | 6 | 113 |
| `phase-1-server-cleanup` | 2026-05-02 | 6 | 93 |
| `phase-2-complete` | 2026-05-02 | 5 | 92 |
| `phase-2-remediation` | 2026-04-30 | 2 | 94 |
| `phase-21-audit-readiness-plan` | 2026-06-17 | 9 | 0 |
| `phase-3-complete` | 2026-05-02 | 10 | 91 |
| `phase-4-complete` | 2026-05-02 | 8 | 90 |
| `phase-consolidation` | 2026-03-27 | 6 | 94 |
| `phase-upgrades` | 2026-03-26 | 1 | 94 |
| `phase0-baseline` | 2026-05-07 | 1 | 31 |
| `phase0-baseline-v2` | 2026-05-07 | 1 | 30 |
| `phase1-paper-candidate` | 2026-05-08 | 11 | 29 |
| `phase10-secrets-hardening` | 2026-05-21 | 5 | 18 |
| `phase11-dry-run-dress-rehearsal` | 2026-05-21 | 4 | 17 |
| `phase12-tiny-manual-live-pilot-control` | 2026-05-21 | 3 | 16 |
| `phase13-pilot-review-release-criteria` | 2026-05-21 | 3 | 15 |
| `phase14-production-operations-hardening` | 2026-05-21 | 3 | 14 |
| `phase15-controlled-manual-live-release` | 2026-05-21 | 3 | 13 |
| `phase16-autonomous-gate-design` | 2026-05-21 | 3 | 12 |
| `phase17-autonomous-shadow-mode` | 2026-05-21 | 3 | 11 |
| `phase18-autonomous-canary-controls` | 2026-05-21 | 3 | 10 |
| `phase19-production-live-release-gate` | 2026-05-21 | 3 | 9 |
| `phase2-worker-runtime` | 2026-05-08 | 6 | 28 |
| `phase3-live-readonly` | 2026-05-08 | 8 | 27 |
| `phase4-5-live-lifecycle` | 2026-05-08 | 4 | 25 |
| `phase4-manual-live-readiness` | 2026-05-08 | 3 | 26 |
| `phase5-manual-live-pilot-readiness` | 2026-05-18 | 4 | 23 |
| `phase6-manual-live-pilot-workflow` | 2026-05-18 | 5 | 22 |
| `phase7-pilot-review-control` | 2026-05-18 | 5 | 21 |
| `phase8-dashboard-pilot-review-ui` | 2026-05-19 | 4 | 20 |
| `phase9-regression-ci-validation` | 2026-05-21 | 5 | 19 |
| `production-readiness-master-plan` | 2026-05-21 | 1 | 6 |
| `production-readiness-validation-pass` | 2026-05-21 | 2 | 8 |
| `production-roadmap` | 2026-05-04 | 1 | 42 |
| `research-leakage-guards-v3` | 2026-06-04 | 10 | 0 |
| `research-registry-replay-v3` | 2026-06-04 | 8 | 0 |
| `research-robustness-audit-v3` | 2026-06-04 | 11 | 0 |
| `resolve-issue-39-live-lifecycle` | 2026-05-09 | 1 | 24 |
| `security-hardening-pass` | 2026-05-04 | 9 | 43 |
| `stabilize-codescan-workflow` | 2026-05-02 | 1 | 87 |
| `strategy-v3-foundation` | 2026-06-04 | 9 | 0 |
| `update-readme-progress` | 2026-05-21 | 1 | 7 |
