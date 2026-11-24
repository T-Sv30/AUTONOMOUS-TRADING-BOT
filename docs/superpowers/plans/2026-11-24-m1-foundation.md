# Milestone 1: Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every backend and frontend check runs on every pull request and passes, with dead code and committed build artifacts removed.

**Architecture:** Keep `.github/workflows/ci.yml` as the single CI pipeline (it already covers backend tests with MongoDB, lint, security, dependency audit, frontend and Docker). Delete the overlapping workflows. Fix the code until the existing pipeline is green, without weakening it.

**Tech Stack:** GitHub Actions, Python 3.12, pytest, ruff, bandit, pip-audit, Node 20, craco/jest.

**Spec:** `docs/superpowers/specs/2026-11-24-premium-upgrade-design.md` (Milestone 1)

## Global Constraints

- Do not weaken any gate: no deleting or skipping tests to get green, no lowering `--cov-fail-under=70`, no removing ruff/bandit/pip-audit steps, no relaxing kill switch or live-trading defaults.
- Do not delete remote branches. Task 4 only produces a list.
- Work on branch `feat/m1-foundation`; one pull request to `main`; the owner merges.
- The phone cannot run the backend tests. CI is the source of truth.

## Review Focus

1. A test that only passed because of a cached `__pycache__` or import side effect must still pass after Task 2's deletions (CI runs from a clean checkout, which covers this).
2. Removing `bot_engine_legacy` must not change `/api` routes: the route import validation test (`tests/test_phase9_route_import_validation.py`) must stay green.
3. A fix for a failing test must fix the code, not the assertion, unless the assertion is provably wrong; each such case is noted in the PR body.
4. Dependency audit failures are fixed by upgrading the pin to a non-vulnerable version, and the full suite reruns after the upgrade.
5. CI must run on pull requests opened from a personal token. Previous pushes never registered the workflows.

---

### Task 1: One CI pipeline that actually runs

**Files:**
- Modify: `.github/workflows/ci.yml` (add `workflow_dispatch:` under `on:`)
- Delete: `.github/workflows/backend-ci.yml`, `.github/workflows/frontend-ci.yml`, `.github/workflows/python-package.yml` (duplicates of `ci.yml` jobs; `python-package.yml` tests Python 3.9 to 3.11 with flake8, which the project doesn't use)
- Keep: `.github/workflows/codescan.yml`

- [ ] **Step 1:** Make the edits above. Commit with the spec and this plan.
- [ ] **Step 2:** Push `feat/m1-foundation` and open a draft PR to `main`.
- [ ] **Step 3:** Confirm the "CI" workflow is registered and running: `gh run list -b feat/m1-foundation` shows a CI run. Expected: a run exists (pass or fail).
- [ ] **Step 4:** Record each failing job and step from `gh run view --log-failed` as the baseline in the PR body.

### Task 2: Remove dead code and build artifacts

**Files:**
- Delete: `backend/services/bot_engine_legacy.py`, `backtesting_engine.py`, `advanced_order_manager.py`, `trading_service.py`, `risk_manager.py`, `enhanced_market_data.py` (all in `backend/services/`; their only importer is `bot_engine_legacy.py`)
- Keep: `backend/services/advanced_risk_manager.py` (used by `api_routes_v2.py:132`)
- Delete from git: all tracked `__pycache__/` files
- Create: `.gitignore` at repo root with `__pycache__/`, `*.pyc`, `.env`, `node_modules/`, `coverage.xml`, `.coverage`, `frontend/build/`

- [ ] **Step 1:** Before deleting, confirm no importers: `grep -rnE "bot_engine_legacy|backtesting_engine|advanced_order_manager|services\.(trading_service|risk_manager|enhanced_market_data)\b" backend --include=*.py` lists only the files being deleted.
- [ ] **Step 2:** Delete, add `.gitignore`, `git rm -r --cached` the `__pycache__` paths. `git ls-files | grep -c __pycache__` prints `0`.
- [ ] **Step 3:** `python -m compileall -q backend` succeeds locally.
- [ ] **Step 4:** Update any README or ARCHITECTURE.md reference to the deleted files.
- [ ] **Step 5:** Commit and push. CI's route import validation test passes.

### Task 3: Get every CI job green

**Files:** decided by the baseline from Task 1.

- [ ] **Step 1:** For each failing test: reproduce from the log, fix the root cause in application code, and push. If a test asserts something provably wrong, fix the test and explain why in the PR body.
- [ ] **Step 2:** For ruff, fix the code. Use per-line `# noqa: <code>` only with a reason in the same comment.
- [ ] **Step 3:** For bandit, fix the finding or add `# nosec <id>` with a reason only for confirmed false positives.
- [ ] **Step 4:** For pip-audit, raise the vulnerable pin in `backend/requirements.txt` to the lowest fixed version; rerun the full suite.
- [ ] **Step 5:** For frontend lint, test or build failures, fix the code.
- [ ] **Step 6:** Verify: `gh pr checks` shows every check passing. Mark the PR ready for review.

### Task 4: Stale branch report

**Files:**
- Create: `docs/superpowers/reports/2026-11-24-stale-branches.md`

- [ ] **Step 1:** For each remote branch other than `main`, record: last commit date, whether it is fully merged into `main` (`git branch -r --merged origin/main`), and commits ahead of `main` (`git rev-list --count origin/main..<branch>`).
- [ ] **Step 2:** Write the report as two tables: "merged, safe to delete" and "has unmerged commits, review first". No branches are deleted.
- [ ] **Step 3:** Commit to the same PR.
