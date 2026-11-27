# Milestone 1.5b: Safety and Data Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the 14 remaining roadmap P0 bugs (P0-2, 5, 6, 7, 8, 10, 11, 12, 17, 18, 19, 20, 21 and 22). Also close the M1.5a carry-overs that must land before members connect real keys in M2.

**Architecture:** Test-first fixes inside the existing services. These are the only structural changes:
- A new `submit_unknown` live order state, with a resolve path.
- The live approval challenge moves from middleware into the live trading service.
- One shared test fakes module.

**Tech Stack:** Python 3.12, FastAPI, Motor/MongoDB, aiohttp, pytest with pytest-asyncio (session loop).

**Spec:** `docs/superpowers/specs/2026-11-24-premium-upgrade-design.md`, plus the owner's "Full-Potential Roadmap (1)" P0 table (Nov 25, 2026) and the M1.5a whole-branch review's M1.5b list (PR #2 body). Exact roadmap fixes are copied into each task.

**Status:** Draft for owner review. The "Decisions for the owner" section lists the calls this plan makes where the roadmap leaves room.

## Process: prove each test fails first, in CI

This is the same process as M1.5a, which the owner approved. The phone cannot run the backend suite.

1. Commit the new or changed tests alone and push. Wait for that commit's CI run with `gh run watch <id> --exit-status`.
2. Confirm with `gh run view <id> --log-failed` that each new test fails on the bug's **behavior**. A TypeError or AttributeError on a not-yet-added argument does not count. Shape the test so the red run shows the behavior. If that is impossible, say why in the report.
3. Commit the fix. Its message ends with `Failing first: <run URL>`. Push and wait for green.
4. Never push the fix while the test-only run is in progress. CI's concurrency group would cancel the earlier run, and the proof with it.
5. A per-task reviewer checks both runs. A final whole-branch review runs at the end, followed by one fix pass. Do not merge.

## Global Constraints

- **Branch:** `feat/m15b-safety-data`. It is cut from `feat/m15a-paper-trading` and gets rebased onto `main` after PR #2 merges: `git rebase --onto main feat/m15a-paper-trading feat/m15b-safety-data`.
- **Safety only gets stricter.** Nothing may weaken the kill switch, the live gate, approval challenges or the paper-to-live stages. Every change here makes them stricter.
- **No live exchange calls in tests or CI.** Live adapter tests use a fake session.
- **Index migrations must not crash existing deployments.** A new unique index that fails on existing duplicates must be reported in the `ensure_indexes` result with its error, not raised. The PR body says how to clean up duplicates.
- **Defaults fail closed.** Unset or malformed safety settings resolve to the safe value: kill switch on, halted, not dry-run treated as live for authorization.
- **CI is the source of truth.** It runs Python 3.12.

## Review Focus

1. **Live order body shapes.** Bodies with `"dry_run": 0`, `"false"`, `"no"`, `"off"`, `null`, or no `dry_run` key at all. All except the missing key must be rejected with 422. A missing key means dry-run (model default `True`). Tests in Task 2.
2. **Restart after an emergency halt.** The user and admins expect live orders to stay blocked until an ops admin releases the halt, even after a restart. Test in Task 4.
3. **The exchange times out after the order was sent.** The order must end in `submit_unknown` with a user halt, and must never be retried blind. Test in Task 5.
4. **Existing database with duplicate emails or forked audit chains.** `ensure_indexes` completes, reports the failed index, and the app still starts. Tests in Tasks 7, 8 and 11.
5. **Attacker tries 8 bad passwords against someone else's email from one IP.** The real owner, logging in from another IP, can still log in. Test in Task 13.

## Decisions for the owner

These are the plan's own calls where the roadmap leaves room. Approve or change them before execution.

1. **Short tokens, not refresh tokens.** P0-21 calls for "short tokens with refresh". This plan shortens access tokens to `JWT_EXPIRE_HOURS` (default 12) and adds revocation through a per-user `token_version`. It defers refresh tokens and httpOnly cookies to M2, because M2 rebuilds accounts and the frontend login.
2. **No order cancelling on emergency halt.** P0-6 says the halt "cancels open orders". The bot only sends market orders, so there are no resting orders to cancel. This plan writes the halt record that blocks every later live order, and adds an ops-admin release. Cancelling resting orders comes with limit orders in M5.
3. **No scheduler for audit verification.** P0-19 asks for a "scheduled verify". There is no scheduler yet; the roadmap's Architecture section plans one. This plan fixes the fork and adds full-chain verification behind an ops endpoint. Scheduling waits for the scheduler.
4. **Shadow-mode allowlist.** P0-7 asks for an allowlist "from the plug-in registry". The registry arrives in M4, so this plan derives the allowlist from the ensemble's strategy names now.

---

### Task 1: Shared test fakes

**Files:**
- Create: `backend/tests/fakes.py`
- Modify: `backend/tests/test_bot_engine_v2.py`, `backend/tests/test_phase1_market_data.py` (import from `fakes`, delete local copies)
- Test: `backend/tests/test_fakes.py`

**Interfaces:**
- Produces: `FakeDB` with on-demand collections and `FakeCollection`. Supports `insert_one` (raises `DuplicateKeyError` for keys registered by `add_unique(fields)`), `find_one(query, projection=None, sort=None)`, `find(...).sort().limit().to_list()`, `update_one/update_many` with `$set`, `$inc` and upsert, `delete_one`, `count_documents`, `find_one_and_update`, and `bulk_write(ops, ordered=)`. Queries support equality, `$ne`, `$gte`, `$lte` and `$in`. `bulk_write` reads `UpdateOne` through its public `_doc`/`_filter` only inside `fakes.py`, so private access sits in one place. `create_index` is recorded.
- Every later task's new unit tests use `fakes.FakeDB`.

- [ ] **Step 1: Write the tests.** Write `test_fakes.py` to cover each query operator, upsert, unique-key `DuplicateKeyError`, `bulk_write` upserts, and sort/limit. These are new tests of new code, so a red run is the import error. Record that in the report.
- [ ] **Step 2:** Move the two test files onto `fakes.FakeDB`. They must stay green with unchanged assertions.
- [ ] **Step 3:** Push, wait for green, and commit with the message `test: shared FakeDB for unit tests`.

### Task 2: Close the `dry_run` coercion bypass (P0-2)

**Files:**
- Modify: `backend/api_routes_v3.py:70-85` (`LiveMarketBuyRequest`, `LiveMarketSellRequest`)
- Modify: `backend/services/scope_enforcement_middleware_v2.py` (`required_scope`, `is_non_dry_run_live_order`, `verify_live_approval`, `dispatch`)
- Modify: `backend/services/live_trading_service_v2.py` (`_place` or equivalent before the gate)
- Test: `backend/tests/test_scope_enforcement_middleware_v2.py`, `backend/tests/test_live_approval_challenges_v2.py`

**Roadmap fix:** "StrictBool on dry_run; verify the challenge in the service layer, not only middleware."

**Interfaces:**
- Produces: `dry_run: StrictBool = True` on both request models.
- Produces: middleware treats a live order as non-dry-run whenever the body has a `dry_run` key whose value is not literally `True`. It requires `trading:live-execute` scope for those.
- Produces: `LiveTradingServiceV2` calls `LiveApprovalChallengeServiceV2(db).verify_and_consume(...)` for every non-dry-run order, before the gate. On `LiveApprovalChallengeError` it returns `{"success": False, "status": "approval_rejected", ...}` with an audit record. The middleware no longer consumes the challenge, because consuming it twice would fail.

- [ ] **Step 1: Write failing tests**

```python
@pytest.mark.parametrize("value", [0, "false", "no", "off", None])
async def test_live_order_rejects_non_boolean_dry_run(client, auth_headers, value):
    r = await client.post("/api/live-trading/market-buy", headers=auth_headers,
                          json={"symbol": "BTC-USD", "notional_usd": 10, "dry_run": value})
    assert r.status_code == 422

async def test_service_rejects_non_dry_run_without_valid_challenge(db):
    result = await LiveTradingServiceV2(db).place_market_buy("u1", "BTC-USD", 10.0,
                                                             approval_token="not-a-challenge", dry_run=False)
    assert result["status"] == "approval_rejected"
```

Today the first test returns 200 or 403 with a dry-run preview. The second reaches the gate, which only checks that a token is present.

- [ ] **Step 2:** Push the tests alone. Confirm the behavioral red: status 200 or 403, not 422, and `status != "approval_rejected"`.
- [ ] **Step 3:** Implement the changes listed above. Update existing middleware tests that relied on the middleware consuming the challenge.
- [ ] **Step 4:** Push the fix and confirm green.

### Task 3: The live kill switch fails closed (P0-5)

**Files:**
- Modify: `backend/services/coinbase_live_execution_adapter_v2.py:21-37`
- Test: `backend/tests/test_phase6_gated_live_execution.py`

**Roadmap fix:** "Default on; read one settings object."

**Interfaces:**
- Produces: `live_order_kill_switch_enabled()` returns True when `COINBASE_LIVE_ORDER_KILL_SWITCH` is unset, empty or malformed. It returns False only for an explicit false value (`0`, `false`, `no`, `off`). It must use the same parsing as `SettingsV2.coinbase_live_order_kill_switch`, so lift one helper both call.

- [ ] **Step 1: Write the failing test.** `test_kill_switch_unset_blocks_live_post`: delete the env var. `_post_private` must raise `CoinbaseLiveExecutionError` mentioning the kill switch before any session is created. Use a fake `session_factory` that fails the test if it is called.
- [ ] **Step 2:** Push the test and confirm the red. Today the fake session is called.
- [ ] **Step 3:** Implement, push, and confirm green.

### Task 4: Emergency halt blocks live orders until released (P0-6)

**Files:**
- Modify: `backend/api_routes_v3.py:291-297` (`/ops/emergency-halt`), plus a new route `/ops/emergency-halt/release`
- Modify: `backend/services/live_pre_submit_safety_service_v2.py` (add `release_halts`)
- Modify: `backend/services/scope_enforcement_middleware_v2.py` (the release route requires `ops:halt`)
- Test: `backend/tests/test_live_order_state_and_safety_v2.py`, `backend/tests/test_scope_enforcement_middleware_v2.py`

**Roadmap fix:** "Halt writes live_halts (user and global), sets a DB kill switch, cancels open orders." See Decision 2 for open orders.

**Interfaces:**
- Produces: the emergency halt route calls `LivePreSubmitSafetyServiceV2(db).create_halt(user_id=<user>, scope="user", reason=..., created_by=<user id>)`, in addition to today's bot deactivation and alert.
  - An ops admin may pass `"scope": "global"`, with `user_id=None`.
  - `halt_record` already finds both user and global halts. Verify that it also finds global ones, and fix it if not.
- Produces: `release_halts(*, user_id: str | None, scope: str, released_by: str) -> int` sets `active: False` and `released_at`/`released_by`. The new route `POST /api/ops/emergency-halt/release` requires `ops:halt` and writes an alert.

- [ ] **Step 1: Write failing tests.**
  - After `POST /api/ops/emergency-halt`, `LivePreSubmitSafetyServiceV2(db).check(user_id)` raises, or returns not allowed, with `no_active_halt` failing.
  - After release, the same check passes.
  - A global halt blocks a different user.
- [ ] **Step 2:** Push and confirm the red. Today the check passes after an emergency halt.
- [ ] **Step 3:** Implement, push, and confirm green.

### Task 5: Unknown live order outcomes are recorded, halted and resolved, never retried blind (P0-8)

**Files:**
- Modify: `backend/services/coinbase_live_execution_adapter_v2.py` (`_post_private`, new `get_order_by_client_oid`)
- Modify: `backend/services/live_order_state_service_v2.py` (new state `SUBMIT_UNKNOWN = "submit_unknown"`, and its allowed transitions)
- Modify: `backend/services/live_trading_service_v2.py`, `backend/services/live_manual_order_lifecycle_service_v2.py`
- Modify: `backend/api_routes_v3.py` (new `POST /api/live-trading/orders/{live_order_id}/resolve`, requiring `trading:live-execute`)
- Test: create `backend/tests/test_live_unknown_outcome_v2.py`

**Roadmap fix:** "Timeout; catch everything; mark UNKNOWN; look up by client order ID before any retry."

**Interfaces:**
- Produces: `class CoinbaseLiveOrderUnknownOutcome(CoinbaseLiveExecutionError)`.
  - `_post_private` uses one session with `aiohttp.ClientTimeout(total=LIVE_ORDER_TIMEOUT_SECONDS)`, default 10, from the env.
  - It raises `CoinbaseLiveOrderUnknownOutcome` for any failure after the request was sent: a timeout, a connection drop, a 5xx, or a body that is not JSON.
  - A 4xx with a JSON body stays a plain `CoinbaseLiveExecutionError`, meaning rejected.
- Produces: `get_order_by_client_oid(client_oid) -> dict | None`, using `GET /orders/client:<client_oid>`. It returns None on 404.
- Produces state transitions:
  - `approved` → `submit_unknown`
  - `submitted` → `submit_unknown`
  - `submit_unknown` → `acknowledged`, `filled`, `rejected` or `failed`
- Produces: on an unknown outcome, the service:
  - moves the order to `submit_unknown`
  - writes an audit record
  - creates a user `live_halt` with reason `unknown order outcome <live_order_id>`
  - emits a critical alert
  - returns `{"success": False, "status": "submit_unknown", "live_order_id": ...}`
- Produces: `resolve_unknown(user_id, live_order_id)` looks the order up by its stored client order ID.
  - Found: transition to the exchange's state, then release that order's halt.
  - Not found: transition to `failed`, then release the halt.
  - The lookup itself fails: stay in `submit_unknown` and keep the halt.
  - It never re-submits.

- [ ] **Step 1: Write failing tests.** Use a fake session that:
  - (a) raises `asyncio.TimeoutError` on POST,
  - (b) returns 502 with an HTML body,
  - (c) returns 400 with JSON.

  Assert:
  - (a) and (b) end in `submit_unknown` with an active user halt, and a second order for that user is blocked before reaching the adapter.
  - (c) ends `rejected` with no halt.
  - `resolve_unknown`, with a fake lookup returning a filled order, moves the order to `filled` and releases the halt. With 404, it moves the order to `failed`.
- [ ] **Step 2:** Push and confirm the behavioral red. Today (a) and (b) end in `adapter_error` with no halt, and (b) raises outside the adapter error path.
- [ ] **Step 3:** Implement, push, and confirm green.

### Task 6: Shadow-mode allowlist matches the strategies the bot runs (P0-7)

**Files:**
- Modify: `backend/services/strategy_ensemble_v2.py` (add `STRATEGY_NAMES = frozenset({"trend_following", "mean_reversion", "breakout"})`)
- Modify: `backend/services/phase17_autonomous_shadow_mode_service_v2.py:37`
- Test: `backend/tests/test_phase17_shadow_mode.py`

**Interfaces:**
- Produces: `ALLOWED_STRATEGIES = StrategyEnsembleV2.STRATEGY_NAMES`. Remove `ma_cross_risk_managed_v1` from the allowlist and update the existing tests that used it.

- [ ] **Step 1: Write the failing test.** A shadow decision from `breakout` is accepted, and one from `ma_cross_risk_managed_v1` is rejected as not allowlisted.
- [ ] **Step 2:** Push and confirm the red. Today the reverse happens.
- [ ] **Step 3:** Implement, push, and confirm green.

### Task 7: TTL indexes that actually expire (P0-10)

**Files:**
- Modify: `backend/auth_core.py:69` (`record_auth_failure`)
- Modify: `backend/services/live_approval_challenge_service_v2.py:~117`
- Modify: `backend/services/mongo_indexes_v2.py:57,62`
- Test: `backend/tests/test_phase9_mongo_index_validation.py`, plus the auth and challenge unit tests

**Roadmap fix:** "Store datetime objects."

**Interfaces:**
- Produces: auth failures also store `created_at_ts: datetime` (aware UTC). Approval challenges also store `expires_at_ts: datetime`. The existing ISO fields stay for current readers.
- Produces new TTL indexes, with the old string-keyed TTL indexes dropped by name inside `ensure_indexes`:
  - `auth_failures_ttl_ts` on `created_at_ts` with `expireAfterSeconds=3600`
  - `live_approval_expiry_ttl_ts` on `expires_at_ts` with `expireAfterSeconds=0`
- Produces: `ensure_indexes` wraps each `create` and drop. Failures go into `result["errors"]` as `{name, error}` rather than raising. This is the migration rule in Global Constraints.

- [ ] **Step 1: Write failing tests.**
  - The stored auth failure and challenge documents carry `datetime` values in `created_at_ts` and `expires_at_ts`.
  - `ensure_indexes` creates the two new TTL indexes with those options.
  - `ensure_indexes` with a FakeDB whose `create_index` raises for one name still returns, and lists that error.
- [ ] **Step 2:** Push and confirm the red: fields missing, `ensure_indexes` raises.
- [ ] **Step 3:** Implement, push, and confirm green.

### Task 8: Missing core indexes and unique users (P0-11)

**Files:**
- Modify: `backend/services/mongo_indexes_v2.py`
- Modify: `backend/auth_core.py` (`signup`)
- Modify: `backend/services/portfolio_service_v2.py:~127` (`risk_metrics` gets `created_at_ts` for TTL)
- Test: `backend/tests/test_phase9_mongo_index_validation.py`, `backend/tests/test_e2e_paper_smoke.py` (real Mongo signup race)

**Roadmap fix:** "Add indexes; TTL or roll-up on risk_metrics."

**Interfaces:**
- Produces these indexes:
  - `uniq_users_email` on `users.email`
  - `uniq_users_id` on `users.id`
  - `uniq_bot_config_user` on `bot_configs.user_id`
  - `risk_metrics_user_timestamp` on `(user_id, timestamp desc)`
  - `risk_metrics_ttl` on `risk_metrics.created_at_ts`, with `expireAfterSeconds` from `RISK_METRICS_RETENTION_DAYS`, default 30, converted to seconds
- Produces: `signup` catches `DuplicateKeyError` on the user insert and returns the same response as "already registered". Task 13 makes that response generic.

- [ ] **Step 1: Write failing tests.**
  - All five indexes are created with these options.
  - On real Mongo, two concurrent signups with one email, run with `asyncio.gather` after `ensure_indexes`, leave exactly one user.
- [ ] **Step 2:** Push and confirm the red: indexes are missing, and the race leaves two users.
- [ ] **Step 3:** Implement, push, and confirm green.

### Task 9: Backtest Sharpe and Sortino annualize by timeframe (P0-12)

**Files:**
- Modify: `backend/services/backtesting_service_v2.py:260,306`
- Test: `backend/tests/test_phase2_backtesting_engine.py`

**Interfaces:**
- Produces: `periods_per_year(timeframe: str) -> float` returns `365 * 86400 / MarketDataService.granularity_for(timeframe)`. Both Sharpe sites use it with the run's timeframe.

- [ ] **Step 1: Write the failing test.** For a fixed return series run at `1h`, Sharpe equals `mean / stdev * sqrt(8760)`, within 1e-6. Today it is `sqrt(525600)`, about 7.75 times larger.
- [ ] **Step 2:** Push and confirm the red.
- [ ] **Step 3:** Implement, push, and confirm green.

### Task 10: `/readyz` checks something (P0-17)

**Files:**
- Modify: `backend/app_factory.py:25-27`
- Test: `backend/tests/test_public_readiness_endpoint.py`

**Roadmap fix:** "Ping Mongo, check heartbeat and settings."

**Interfaces:**
- Produces: `GET /readyz` returns 200 `{"status": "ready", ...}` only when the Mongo ping succeeds and settings validated. Otherwise it returns 503 with the failing check names.
  - `GET /readyz?strict=true` also requires a worker heartbeat fresher than `WorkerHeartbeatServiceV2`'s stale threshold. It delegates to `OperationalReadinessServiceV2.readiness(db, strict=True)` for the environment checks.
  - The response contains no secrets.
- Docker healthchecks keep using `/healthz`, so compose smoke behavior is unchanged.

- [ ] **Step 1: Write failing tests.**
  - With `app_state.db` patched to a client whose `command("ping")` raises, `/readyz` returns 503.
  - `strict=true` with no heartbeat returns 503.
  - `/readyz` on the real CI Mongo returns 200.
- [ ] **Step 2:** Push and confirm the red. Today it always returns 200.
- [ ] **Step 3:** Implement, push, and confirm green.

### Task 11: The audit chain cannot fork, and verification covers the whole chain (P0-19)

**Files:**
- Modify: `backend/services/live_order_audit_service_v2.py:35-75`
- Modify: `backend/services/mongo_indexes_v2.py:39` (replace the non-unique `live_audits_user_previous_hash`)
- Modify: `backend/api_routes_v3.py` (new `GET /api/ops/audit-chain/verify`, requiring `ops:readiness`)
- Test: `backend/tests/test_live_order_state_and_safety_v2.py`, plus a real Mongo concurrency test

**Roadmap fix:** "Unique index plus retry; sequence number; scheduled verify." For the schedule, see Decision 3.

**Interfaces:**
- Produces: each record gets `sequence: int`, which is the previous record's sequence plus 1, starting at 1.
  - `append` retries up to 5 times on `DuplicateKeyError`, re-reading the latest record each time.
  - New unique index `uniq_live_audit_user_sequence` on `(user_id, sequence)`.
  - New unique index `uniq_live_audit_user_previous_hash` on `(user_id, previous_hash)`, with the old non-unique one dropped by name.
  - Errors are reported per Task 7's rule.
- Produces: `latest_for_user` sorts by `sequence` desc, then `created_at` desc for legacy records.
- Produces: `verify_user_chain(user_id)` pages through all records in batches of 500, ordered by sequence, with no cap.

- [ ] **Step 1: Write failing tests.**
  - Twenty concurrent `append` calls for one user on real Mongo, after `ensure_indexes`, produce one linear chain. `verify_user_chain` reports no issues and the sequences run 1 to 20.
  - With 1,200 records in the FakeDB, verification checks all of them and detects a tampered record at index 1,100.
- [ ] **Step 2:** Push and confirm the red. Today the chain forks, and the 1,100th record goes unchecked.
- [ ] **Step 3:** Implement, push, and confirm green.

### Task 12: Bot config cannot start bots or set unsafe values, and list limits are capped (P0-20)

**Files:**
- Modify: `backend/api_routes_v2.py:10-25,56-71` (`BotConfig`, `POST /bot-config`)
- Modify: `backend/api_routes_v2.py`, `backend/api_routes_v3.py` (every `limit: int` query parameter)
- Test: create `backend/tests/test_bot_config_validation.py`

**Roadmap fix:** "Separate start/stop; validate; cap limits."

**Interfaces:**
- Produces: a new `BotConfigUpdate` model. `POST /bot-config` uses it and `$set`s only the fields provided (`exclude_unset`).
  - It has no `is_active` field. Only `/bot/start` and `/bot/stop` change that. It still accepts and ignores `capital_floor`.
  - `symbols` must be non-empty, have no duplicates, and come from `PaperExecutionAdapterV2.SUPPORTED_SYMBOLS`.
  - `max_daily_loss` is in (0, 0.10].
  - `max_drawdown_pct` is in (0, 0.25].
  - `max_position_notional` is in [5, 10000].
  - `cooldown_seconds` is in [0, 86400].
  - Out-of-range values return 422.
- Produces: every list `limit` parameter is `Query(100, ge=1, le=500)`. Keep each route's existing default.

- [ ] **Step 1: Write failing tests.**
  - Posting `{"is_active": true}` does not activate the bot.
  - Posting `symbols: ["DOGE-USD"]` returns 422.
  - Posting `max_daily_loss: 5` returns 422.
  - Posting only `symbols` leaves the other stored fields unchanged.
  - `GET /api/alerts?limit=100000` returns 422.
- [ ] **Step 2:** Push and confirm the red.
- [ ] **Step 3:** Implement, push, and confirm green. Update `test_e2e_paper_smoke.py` if it relies on `/bot-config` activating the bot.

### Task 13: Login lockout cannot be weaponized, signup does not reveal accounts, tokens are shorter and revocable (P0-21)

**Files:**
- Modify: `backend/auth_core.py`
- Modify: `backend/api_routes_v2.py` (pass the client IP into login and signup; new `POST /api/auth/logout-all`)
- Test: create `backend/tests/test_auth_hardening.py`

**Roadmap fix:** "Per-IP plus per-account backoff; generic errors; short tokens with refresh." For refresh tokens, see Decision 1.

**Interfaces:**
- Produces: failures are recorded with `email`, `ip` and `created_at_ts`.
  - Login is throttled when the same `(email, ip)` pair has 8 or more failures in the window, or the same IP has 30 or more failures across all emails.
  - Failures from other IPs never lock out an `(email, ip)` pair that has no failures.
  - `request.client.host` is the IP. Do not trust `X-Forwarded-For` until the proxy work in a later milestone.
- Produces: signup with an existing email returns 400 `{"detail": "Could not create account"}`, the same response as any other signup failure.
  - Fully hiding which emails are registered would need email delivery, so response timing is not addressed. Record this limit in the PR body.
  - M2's invite-only signup removes open registration.
- Produces: `create_access_token` uses `JWT_EXPIRE_HOURS`, default 12, validated in `SettingsV2` to be 1–168.
  - Tokens carry `tv` (`token_version`).
  - `get_current_user` rejects a token whose `tv` differs from the user's stored `token_version`. A missing stored value counts as 0.
  - `logout-all` increments the user's `token_version`.

- [ ] **Step 1: Write failing tests.**
  - Eight bad logins for victim@x from IP A, then a correct login from IP B, returns 200.
  - Signup with an existing email returns `"Could not create account"`.
  - A token's `exp` is 12 hours out.
  - After `logout-all`, the old token returns 401.
- [ ] **Step 2:** Push and confirm the red.
- [ ] **Step 3:** Implement, push, and confirm green.

### Task 14: Delete `sitecustomize.py` (P0-22)

**Files:**
- Delete: `backend/sitecustomize.py`
- Modify: `backend/tests/test_phase1_hardening.py:55-100` (replace the two sitecustomize tests)
- Check: `backend/app_factory.py`, so that `runtime_config` is imported at app creation and settings validation still runs at startup

**Interfaces:**
- Produces two replacement tests:
  - `create_app()`'s CORS middleware uses `allow_origins == SETTINGS.cors_origins`, the methods `["GET", "POST", "OPTIONS"]`, and headers that include `Authorization`.
  - `GET /api/market-data/BTC-USD` without a token returns 401, through the route's own `Depends(get_current_user)`.

- [ ] **Step 1:** Write the replacement tests. They pass today, because sitecustomize duplicates app behavior. Prove they guard the app's own behavior by pushing a throwaway commit that deletes sitecustomize and removes `Depends(get_current_user)` from the market-data route. Confirm the 401 test fails, then revert the throwaway.
- [ ] **Step 2:** Delete `sitecustomize.py` and the two old tests. Push and confirm green, including compose smoke.

### Task 15: Bot engine claims the order before sending it (M1.5a carry-over)

**Files:**
- Modify: `backend/services/bot_engine.py` (`execute_buy`, `execute_sell`)
- Modify: `backend/services/portfolio_service_v2.py` (new `claim_trade`, and `record_trade_attempt` updates the claim)
- Test: `backend/tests/test_bot_engine_v2.py`

**Why:** The M1.5a review requires this before the engine ever drives a live adapter. Today, the `DuplicateKeyError` backstop fires after the order has already gone to the adapter.

**Interfaces:**
- Produces: `claim_trade(user_id, symbol, side, idempotency_key, client_order_id, context) -> bool`.
  - It inserts a `trades_v2` document with `status: "pending"` and `idempotency_claim: True`.
  - It returns False on `DuplicateKeyError`.
- `execute_buy` and `execute_sell` call it after the lock and before the adapter.
  - If it returns False, log `duplicate_order_skipped`, emit a `duplicate_order_blocked` warning alert, and return.
  - After the adapter call, `record_trade_attempt` updates that pending document by `idempotency_key` instead of inserting a new one.
- A rejected result sets `status: "rejected"` and `idempotency_claim: False`, so a retry on the same candle can claim again.

- [ ] **Step 1: Write failing tests.**
  - With a pending claim already present for the key, the adapter is never called (spy on the adapter), and an alert is emitted.
  - A rejected attempt followed by a retry on the same candle reaches the adapter twice and ends with one filled document.
- [ ] **Step 2:** Push and confirm the red. Today the adapter is called first.
- [ ] **Step 3:** Implement, push, and confirm green, including the soak test.

### Task 16: Settings validation, deduplicated alerts, and one engine clock (M1.5a carry-overs)

**Files:**
- Modify: `backend/services/settings_v2.py`, `backend/services/market_data_service.py`, `backend/services/paper_execution_adapter_v2.py`
- Modify: `backend/services/alert_service.py`
- Modify: `backend/services/portfolio_service_v2.py` (`ensure_account_state`), `backend/services/bot_engine.py` (`halt_bot`)
- Test: `backend/tests/test_settings_v2.py`, create `backend/tests/test_alert_service_v2.py`, `backend/tests/test_bot_engine_v2.py`

**Interfaces:**
- Produces: `SettingsV2` validates these settings, and a malformed or out-of-range value fails at startup with the variable named:
  - `MARKET_DATA_TIMEOUT_SECONDS`: 1–120
  - `SIM_SPREAD_BPS`: 0–100
  - `PAPER_EXTRA_SLIPPAGE_BPS`: 0–100
  - `PAPER_FALLBACK_SPREAD_BPS`: 0–100
  - `LIVE_ORDER_TIMEOUT_SECONDS`: 1–60
  - `RISK_METRICS_RETENTION_DAYS`: 1–3650
  - `JWT_EXPIRE_HOURS`: 1–168

  `MarketDataService` and the paper adapter take their defaults from those validated values. The `SIMULATION_MODE` deprecation warning is logged once per process.
- Produces: `AlertService.emit(..., dedupe_seconds: int = 0)`. With a value above 0, a matching open alert (same user, type and symbol context) newer than the window gets `count` incremented and `last_seen_at` set, instead of a new insert. The engine's `market_data_failed` alerts use `dedupe_seconds=300`.
- Produces: `ensure_account_state(user_id, starting_cash=10000.0, now=None)` stamps `daily_start_date`, and a new `starting_cash` field, from `now`. `BotEngine` passes `self._now()`. `halt_bot` stamps `updated_at` from `self._now()`. Task 17 reads `starting_cash`.

- [ ] **Step 1: Write failing tests.**
  - `SIM_SPREAD_BPS=abc` fails settings load with a message naming it.
  - Ten market-data failures in one minute leave one alert with `count == 10`.
  - With the soak test's scripted clock, a new account's `daily_start_date` equals the scripted date with no manual seeding. Remove the seeding from the soak test.
- [ ] **Step 2:** Push and confirm the red.
- [ ] **Step 3:** Implement, push, and confirm green.

### Task 17: Performance metrics use the account's starting cash and count round trips (P0-18)

**Files:**
- Modify: `backend/api_routes_v2.py:137-160`
- Test: create `backend/tests/test_performance_metrics.py`

**Roadmap fix:** "Use the account's starting cash; count round trips."

**Interfaces:**
- Consumes: `portfolio_state.starting_cash` from Task 16, falling back to 10,000 for legacy accounts.
- Produces: ROI is `(total_equity - starting_cash) / starting_cash`.
  - `total_trades` counts filled trades.
  - `win_rate` is winning filled SELLs divided by all filled SELLs. A SELL wins when `realized_pnl > 0`.
  - Rejected and pending trades are excluded.

- [ ] **Step 1: Write the failing test.** An account starting at 5,000 with equity 5,500 shows ROI 10. Two BUYs and two SELLs, one winning, give a win rate of 50, not 25.
- [ ] **Step 2:** Push and confirm the red.
- [ ] **Step 3:** Implement, push, and confirm green.

### Task 18: Pull request

- [ ] **Step 1:** Push `feat/m15b-safety-data` and open a draft PR. Base it on `feat/m15a-paper-trading` while PR #2 is open, or on `main` after PR #2 merges, after rebasing. The body must include:
  - each P0 ID with its failing and green runs
  - the four owner decisions
  - the index migration notes: what fails on duplicate emails or forked audit chains, and how to clean them up
  - the behavior changes: 422 for non-boolean `dry_run`, the kill switch on when unset, a halt until released, 12-hour tokens, `logout-all`, and the `/bot-config` field rules
- [ ] **Step 2:** Confirm every CI job passes on the PR head.
- [ ] **Step 3:** Leave the merge to the owner.
