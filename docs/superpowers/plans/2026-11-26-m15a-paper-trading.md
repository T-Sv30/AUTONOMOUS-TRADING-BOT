# Milestone 1.5a: Make Paper Trading Work Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the engine bugs that stop the paper bot after a few trades (roadmap P0-1, 3, 4, 9, 13, 14, 15, 16), so paper trading runs on live public prices and keeps trading.

**Architecture:** Small, test-first fixes inside the existing services. One new setting, `MARKET_DATA_SOURCE`, replaces `SIMULATION_MODE`. The simulator becomes a function of time, kept for tests and CI. No new services and no change to the live gate path.

**Tech Stack:** Python 3.12, FastAPI, Motor/MongoDB, aiohttp, pytest with pytest-asyncio (session loop, see `backend/tests/conftest.py`).

**Spec:** `docs/superpowers/specs/2026-11-24-premium-upgrade-design.md`, plus the owner's "Autonomous Trading Bot — Full-Potential Roadmap (1)" (Nov 25, 2026), P0 table and "Decided" notes. The decisions this plan relies on are copied into Global Constraints.

## Global Constraints

- Branch from `main` after PR #1 merges. If PR #1 was squash-merged, run `git rebase --onto origin/main feat/m1-foundation feat/m15a-paper-trading`.
- Scope is P0-1, 3, 4, 9, 13, 14, 15 and 16 only. The other 14 P0 bugs belong to M1.5b.
- Each fix starts with a failing test that reproduces the bug. Commit the test with the fix.
- `MARKET_DATA_SOURCE` takes exactly `live`, `simulated` or `replay`. It replaces `SIMULATION_MODE`.
- `.env.example` sets `MARKET_DATA_SOURCE=live`. The CI compose job sets it to `simulated`, because it copies `.env.example`.
- Tests and CI never call Coinbase. They use `simulated`.
- Store every candle the bot sees, so a paper week can be replayed.
- Paper fills price from the exchange's current bid and ask, not last trade plus a fixed allowance.
- Do not weaken the kill switch, the live gate or any pre-submit check. Do not touch the live adapters.
- CI on GitHub (Python 3.12) is the source of truth. The owner's phone cannot run the backend suite.

## Review Focus

1. **Exchange unreachable in live mode.** One symbol's candles fail. The bot should skip that symbol, alert, and still process the others. Test in Task 4.
2. **Old `.env` files.** `SIMULATION_MODE=True` is set and `MARKET_DATA_SOURCE` is not. Settings should load, log a warning naming `MARKET_DATA_SOURCE`, and use `live`. Test in Task 1.
3. **Typo in the setting.** `MARKET_DATA_SOURCE=sim` should fail at startup with a message listing the allowed values. Test in Task 1.
4. **Bot restarted days later.** Daily start equity should roll to the current equity, not trip a "daily loss" halt for losses from earlier days. Test in Task 7.
5. **Dropping the open candle shortens history.** The planner asks for 120 candles and should still get 120 closed ones. Test in Task 3.

---

### Task 1: `MARKET_DATA_SOURCE` replaces `SIMULATION_MODE`

**Files:**
- Modify: `backend/services/market_data_service.py` (constructor, module constants)
- Modify: `backend/services/settings_v2.py` (`simulation_mode` field, `from_env`, `redacted_report`)
- Modify: `backend/runtime_config.py:52`
- Modify: `backend/services/operational_readiness_v2.py:36`
- Modify: `backend/tests/conftest.py`; the `SIMULATION_MODE` lines in `test_live_approval_challenges_v2.py`, `test_scope_enforcement_middleware_v2.py`, `test_e2e_paper_smoke.py`, `test_request_errors_v2.py`, `test_phase7_operational_readiness.py`, `test_phase10_hardening_fail_closed.py`; the `simulation_mode=True` call sites in `test_phase1_market_data.py`
- Modify: `.github/workflows/ci.yml` (job env, compose-smoke job), `.env.example`, `backend/.env.example`, `README.md:498,513`, `docs/CONFIGURATION.md:83,93`
- Test: `backend/tests/test_settings_v2.py`, `backend/tests/test_phase1_market_data.py`

**Interfaces:**
- Produces: `MARKET_DATA_SOURCES = ("live", "simulated", "replay")` and `parse_market_data_source(value: str | None) -> str` in `market_data_service.py`. An unset value returns `"live"`. Any other value raises `ValueError` listing the three allowed values.
- Produces: `MarketDataService(db=None, source: str | None = None, seed=None, clock=None, session_factory=None, timeout_seconds: float | None = None)`. `source=None` reads `MARKET_DATA_SOURCE`. The attribute `self.source` replaces `self.simulation_mode`. `clock` and `session_factory` are used in Tasks 2 and 3.
- Produces: `SettingsV2.market_data_source: str`, validated through `parse_market_data_source`. It replaces `simulation_mode`.

- [ ] **Step 1: Write failing tests**

```python
# test_settings_v2.py
def test_market_data_source_defaults_to_live(monkeypatch):
    monkeypatch.delenv("MARKET_DATA_SOURCE", raising=False)
    monkeypatch.delenv("SIMULATION_MODE", raising=False)
    assert SettingsV2.from_env().market_data_source == "live"

def test_market_data_source_rejects_unknown_value(monkeypatch):
    monkeypatch.setenv("MARKET_DATA_SOURCE", "sim")
    with pytest.raises(ValueError, match="live, simulated, replay"):
        SettingsV2.from_env()

def test_legacy_simulation_mode_is_ignored_with_warning(monkeypatch, caplog):
    monkeypatch.delenv("MARKET_DATA_SOURCE", raising=False)
    monkeypatch.setenv("SIMULATION_MODE", "True")
    assert SettingsV2.from_env().market_data_source == "live"
    assert "MARKET_DATA_SOURCE" in caplog.text

# test_phase1_market_data.py
def test_market_data_service_reads_source_from_env(monkeypatch):
    monkeypatch.setenv("MARKET_DATA_SOURCE", "replay")
    assert MarketDataService().source == "replay"
```

Use the same env setup as the existing tests in `test_settings_v2.py`, which already provide a valid `JWT_SECRET` and `DEBUG`.

- [ ] **Step 2: Run them and confirm they fail**

Run: `cd backend && python -m pytest tests/test_settings_v2.py tests/test_phase1_market_data.py -v`
Expected: the four new tests FAIL with an `AttributeError` on `market_data_source` or `source`.

- [ ] **Step 3: Implement**

- Add the constants and `parse_market_data_source` to `market_data_service.py`. Change the constructor to the signature above. Every `if self.simulation_mode:` becomes `if self.source == "simulated":`.
- In `SettingsV2`, replace `simulation_mode` with `market_data_source`. In `from_env`, log a warning when `SIMULATION_MODE` is set and `MARKET_DATA_SOURCE` is not. Update `redacted_report` and `runtime_config.SIMULATION_MODE` (rename to `MARKET_DATA_SOURCE`).
- In `operational_readiness_v2.py`, the `paper_mode_simulation` check becomes `market_data_source_live`. It passes when the source is `live` and warns otherwise, with the message "Paper results only reflect the market when MARKET_DATA_SOURCE=live."
- At the top of `backend/tests/conftest.py`, add `os.environ.setdefault("MARKET_DATA_SOURCE", "simulated")`. In the six listed test files, replace `SIMULATION_MODE` with `MARKET_DATA_SOURCE` and the value `simulated`. Replace `simulation_mode=True` with `source="simulated"` in `test_phase1_market_data.py`.
- In `ci.yml`, change the job env to `MARKET_DATA_SOURCE: simulated`. In compose-smoke, after `cp .env.example .env`, add `sed -i 's/^MARKET_DATA_SOURCE=.*/MARKET_DATA_SOURCE=simulated/' .env`.
- Set `MARKET_DATA_SOURCE=live` in both `.env.example` files. Update the README table row and `docs/CONFIGURATION.md` to describe the three values.

- [ ] **Step 4: Run the full backend suite**

Run: `cd backend && python -m pytest tests -q`
Expected: all tests pass, including the four new ones. `grep -rn SIMULATION_MODE backend .github .env.example docs README.md` finds only the warning in `settings_v2.py`.

- [ ] **Step 5: Commit**

```bash
git add -A backend .github .env.example README.md docs/CONFIGURATION.md
git commit -m "feat(config): MARKET_DATA_SOURCE replaces SIMULATION_MODE; paper defaults to live prices"
```

### Task 2: A simulator that moves with time (P0-3)

**Files:**
- Modify: `backend/services/market_data_service.py` (`_rng_for`, `_simulate_price_data`, `_simulate_candles`)
- Test: `backend/tests/test_phase1_market_data.py`

**Interfaces:**
- Consumes: `MarketDataService(source="simulated", seed=..., clock=...)` from Task 1. `clock` is a zero-argument callable returning an aware UTC `datetime`, defaulting to `utc_now`.
- Produces: `_sim_price(symbol: str, at: datetime) -> float`, the one source of simulated prices. Ticker and candles both use it.
- Produces: simulated tickers carry `bid` and `ask` around `price`, with the spread from `SIM_SPREAD_BPS` (default 2). Task 5 relies on this.

- [ ] **Step 1: Write failing tests**

```python
NOON = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)

def sim(at):
    return MarketDataService(source="simulated", seed="s", clock=lambda: at)

async def test_simulated_ticker_moves_over_time():
    first = await sim(NOON).get_current_price("BTC-USD")
    later = await sim(NOON + timedelta(hours=1)).get_current_price("BTC-USD")
    assert first["price"] != later["price"]

async def test_simulation_is_deterministic_for_same_instant():
    a = await sim(NOON).get_candles("BTC-USD", periods=50)
    b = await sim(NOON).get_candles("BTC-USD", periods=50)
    assert [c["close"] for c in a] == [c["close"] for c in b]

async def test_simulated_ticker_agrees_with_last_candle():
    svc = sim(NOON)
    ticker = await svc.get_current_price("BTC-USD")
    last = (await svc.get_candles("BTC-USD", periods=5))[-1]
    assert abs(ticker["price"] - last["close"]) / last["close"] < 0.005

async def test_overlapping_windows_share_candles():
    a = await sim(NOON).get_candles("BTC-USD", periods=60)
    b = await sim(NOON + timedelta(minutes=10)).get_candles("BTC-USD", periods=60)
    shared = {c["open_time"]: c["close"] for c in a}
    assert all(shared[c["open_time"]] == c["close"] for c in b if c["open_time"] in shared)

async def test_simulated_ticker_has_bid_below_ask():
    t = await sim(NOON).get_current_price("BTC-USD")
    assert t["bid"] < t["price"] < t["ask"]
```

The first, third and fourth tests fail today. The old generator is seeded by `periods` alone, so the price never moves, the ticker sits about 2% from the candles, and the window changes when `periods` does.

- [ ] **Step 2: Run and confirm the failures**

Run: `cd backend && python -m pytest tests/test_phase1_market_data.py -v`
Expected: the new tests FAIL as described.

- [ ] **Step 3: Implement `_sim_price(symbol, at)`**

The tests do not fix the formula, so use this one. Log-price is the base price (BTC 45,000, ETH 2,500, other 1,000) times `exp` of the sum of three sine waves. Their periods are 6 hours, 2 days and 9 days, their amplitudes are 1.5%, 4% and 8%, and their phases are derived from a SHA-256 of `seed:symbol`. Add per-minute noise of ±0.15%, seeded by `seed:symbol:<minute epoch>`. The price is bounded, moves on every timescale the strategies use, and depends only on `(seed, symbol, at)`.

Each candle's open is `_sim_price(open_time)`. Its close is `_sim_price(close_time)`. Its high and low widen the open and close range by seeded noise of up to 0.2%. The ticker is `_sim_price(clock())`. Candles end at the last closed boundary before `clock()`. Delete `_rng_for`.

- [ ] **Step 4: Run the tests**

Run: `cd backend && python -m pytest tests/test_phase1_market_data.py tests/test_phase3_paper_execution_engine.py tests/test_strategy_signals_v2.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/services/market_data_service.py backend/tests/test_phase1_market_data.py
git commit -m "fix(market-data): simulated prices move with time and agree with candles (P0-3)"
```

### Task 3: Shared session, timeout and closed candles only (P0-9)

**Files:**
- Modify: `backend/services/market_data_service.py` (`get_current_price`, `_fetch_coinbase_candles`, new `close`)
- Test: `backend/tests/test_phase1_market_data.py`

**Interfaces:**
- Consumes: `session_factory` and `timeout_seconds` from Task 1. `timeout_seconds=None` reads `MARKET_DATA_TIMEOUT_SECONDS`, default 10.
- Produces: `async def close(self) -> None` closes the shared session. Task 4 calls it from `BotEngine.stop`.
- Produces: live tickers return `bid` and `ask` from the Coinbase ticker response, as floats.

- [ ] **Step 1: Write failing tests**

Write a `FakeSession` that records the kwargs it was built with, counts how often it was built, and returns canned JSON for `/ticker` and `/candles`. The candle rows put the newest row's open time at `NOON - 30s` (still open) and the others at earlier whole minutes.

```python
async def test_live_requests_share_one_session_with_timeout():
    built = []
    svc = MarketDataService(source="live", clock=lambda: NOON,
                            session_factory=lambda **kw: built.append(kw) or FakeSession(...))
    await svc.get_current_price("BTC-USD")
    await svc.get_candles("BTC-USD", periods=5)
    assert len(built) == 1
    assert built[0]["timeout"].total == 10

async def test_live_candles_exclude_the_open_candle():
    candles = await live_svc_with_rows(...).get_candles("BTC-USD", periods=5)
    assert all(parse(c["close_time"]) <= NOON for c in candles)

async def test_live_candles_return_requested_count_of_closed_candles():
    candles = await live_svc_with_rows(...).get_candles("BTC-USD", periods=120)
    assert len(candles) == 120

async def test_live_ticker_carries_bid_and_ask():
    t = await live_svc_with_ticker({"price": "100", "bid": "99.9", "ask": "100.1"}).get_current_price("BTC-USD")
    assert (t["bid"], t["ask"]) == (99.9, 100.1)
```

- [ ] **Step 2: Run and confirm the failures**

Run: `cd backend && python -m pytest tests/test_phase1_market_data.py -v`
Expected: the new tests FAIL. A session is built per call with no timeout, the open candle is returned, and the ticker has no `bid` or `ask`.

- [ ] **Step 3: Implement**

Build one session lazily with `session_factory(timeout=aiohttp.ClientTimeout(total=timeout_seconds))` and reuse it. Request `periods + 1` candles, capped at `MAX_COINBASE_CANDLES`. Drop any candle whose `close_time` is after `clock()`, then return the last `periods`. Add `bid` and `ask` to the live ticker dict. `close()` closes the session if one exists.

- [ ] **Step 4: Run the tests**

Run: `cd backend && python -m pytest tests/test_phase1_market_data.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/services/market_data_service.py backend/tests/test_phase1_market_data.py
git commit -m "fix(market-data): shared session with timeout, closed candles only (P0-9)"
```

### Task 4: The bot stores what it sees, replay reads it back, and one bad symbol doesn't stop the cycle

**Files:**
- Modify: `backend/services/bot_engine.py` (`__init__`, `stop`, `cycle`, `process_symbol`)
- Modify: `backend/services/market_data_service.py` (`get_candles`, `get_current_price` for `replay`)
- Modify: `backend/services/execution_service_v2.py:9-10` (constructor)
- Test: create `backend/tests/test_bot_engine_v2.py`

**Interfaces:**
- Consumes: `MarketDataService(db=..., source=...)` and `close()` from Tasks 1 and 3.
- Produces: `BotEngine.__init__(self, db, market: MarketDataService | None = None)`. The default is `MarketDataService(db=db)`. Tasks 6 and 11 inject a simulated, clocked instance.
- Produces: `ExecutionServiceV2(trading_service: TradingServiceV2 | None = None)`. `BotEngine` builds `ExecutionServiceV2(TradingServiceV2(PaperExecutionAdapterV2(market_data=self.market)))`, so decisions and paper fills read the same prices through one session.
- Produces: with `source="replay"`, `get_candles` returns `get_stored_candles(symbol, timeframe, periods)` and the ticker is the last stored close. Both raise `MarketDataUnavailable` when nothing is stored. A time-travelling replay clock is left for the M4 drift monitor.

- [ ] **Step 1: Write failing tests**

`test_bot_engine_v2.py` holds an in-memory `FakeDB` for this and later tasks. Copy the `FakeCollection` from `test_phase2_portfolio_accounting.py`. Add `find`, `find_one_and_update`, `delete_one`, `update_many` and on-demand collections through `__getattr__`, as in `test_phase7_operational_readiness.py`.

```python
async def test_bot_persists_candles_it_reads():
    db = FakeDB()
    engine = BotEngine(db, market=MarketDataService(db=db, source="simulated", clock=lambda: NOON))
    await engine.process_symbol("u1", "BTC-USD", [], {}, RiskGuardV2(), {"BTC-USD": 45000.0})
    assert len(db.market_candles.docs) == 120

async def test_replay_reads_stored_candles():
    db = FakeDB()
    await MarketDataService(db=db, source="simulated", clock=lambda: NOON).get_candles("BTC-USD", periods=30)
    replay = MarketDataService(db=db, source="replay")
    assert len(await replay.get_candles("BTC-USD", periods=30)) == 30

async def test_failing_symbol_is_skipped_and_others_still_trade():
    # market whose get_historical_data raises MarketDataUnavailable for ETH-USD only
    ...
    await engine.cycle("u1")
    assert any(a["event_type"] == "market_data_failed" for a in db.alerts.docs)
    assert processed_symbols == ["BTC-USD"]

async def test_paper_fills_use_the_engine_market():
    market = MarketDataService(db=FakeDB(), source="simulated", clock=lambda: NOON)
    engine = BotEngine(FakeDB(), market=market)
    assert engine.execution.trading_service.adapter.market_data is market
```

- [ ] **Step 2: Run and confirm the failures**

Run: `cd backend && python -m pytest tests/test_bot_engine_v2.py -v`
Expected: FAIL. The engine builds `MarketDataService()` with no database, `replay` is unknown, one symbol's error aborts the whole cycle, and the paper adapter builds its own market service.

- [ ] **Step 3: Implement**

Accept an injected `market` and default to `MarketDataService(db=db)`. Pass it to the paper adapter as described above. Implement the `replay` branches. In `cycle`, wrap each `process_symbol` call: on `MarketDataUnavailable`, log, emit the same `market_data_failed` alert that `get_price_map` emits, and continue. `stop()` awaits `self.market.close()`.

- [ ] **Step 4: Run the tests**

Run: `cd backend && python -m pytest tests/test_bot_engine_v2.py tests/test_phase1_market_data.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/services/bot_engine.py backend/services/market_data_service.py backend/tests/test_bot_engine_v2.py
git commit -m "feat(bot): store candles the bot reads, add replay source, isolate per-symbol failures"
```

### Task 5: Paper fills at bid and ask

**Files:**
- Modify: `backend/services/paper_execution_adapter_v2.py` (`_reference_price`, `place_market_buy`, `place_market_sell`)
- Test: `backend/tests/test_phase3_paper_execution_engine.py`

**Interfaces:**
- Consumes: tickers with `bid` and `ask` (Tasks 2 and 3).
- Produces: `_reference_quote(symbol) -> dict` with `price`, `bid` and `ask`, where `bid` and `ask` may be `None`. Order results add `"price_basis": "ask" | "bid" | "last_plus_slippage"`.

- [ ] **Step 1: Write failing tests**

```python
async def test_paper_buy_fills_at_ask():
    adapter = PaperExecutionAdapterV2(market_data=FakeQuote(price=100.0, bid=99.9, ask=100.1))
    order = await adapter.place_market_buy("BTC-USD", 50.0)
    assert order["filled_price"] == 100.1 and order["price_basis"] == "ask"

async def test_paper_sell_fills_at_bid():
    ...
    assert order["filled_price"] == 99.9 and order["price_basis"] == "bid"

async def test_paper_fill_falls_back_to_slippage_without_quote():
    adapter = PaperExecutionAdapterV2(market_data=FakeQuote(price=100.0, bid=None, ask=None))
    order = await adapter.place_market_buy("BTC-USD", 50.0)
    assert order["filled_price"] == pytest.approx(100.0 * 1.0008)
    assert order["price_basis"] == "last_plus_slippage"
```

- [ ] **Step 2: Run and confirm the failures**

Run: `cd backend && python -m pytest tests/test_phase3_paper_execution_engine.py -v`
Expected: the first two FAIL because the fill is last price plus 8 bps.

- [ ] **Step 3: Implement**

Buys fill at `ask` and sells at `bid` when present, with no extra slippage. Without a quote, keep today's last price plus or minus the configured slippage. Fees, minimums and partial fills are unchanged.

- [ ] **Step 4: Run the tests**

Run: `cd backend && python -m pytest tests/test_phase3_paper_execution_engine.py tests/test_e2e_paper_smoke.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/services/paper_execution_adapter_v2.py backend/tests/test_phase3_paper_execution_engine.py
git commit -m "fix(paper): price fills from bid and ask, not last trade plus allowance"
```

### Task 6: One order per decision, not one per strategy forever (P0-1)

**Files:**
- Modify: `backend/services/execution_control_v2.py:21-26`
- Modify: `backend/services/bot_engine.py` (`process_symbol` adds the decision time to the plan; `execute_sell` default context)
- Modify: `backend/tests/test_phase3_trading_controls.py:162`, `backend/tests/test_e2e_paper_smoke.py:106-126`
- Test: `backend/tests/test_bot_engine_v2.py`

**Interfaces:**
- Produces: `build_idempotency_key(user_id, symbol, side, signal_snapshot) -> str` returns `f"{user_id}:{symbol}:{side}:{strategy}:{decision_candle_close}"`. `decision_candle_close` is the ISO close time of the last closed candle the plan was built from, read from `signal_snapshot["decision_candle_close"]`. If it is missing, raise `ValueError("signal_snapshot.decision_candle_close is required")`, so no order is placed without a decision time.
- Produces: `process_symbol` sets `plan["decision_candle_close"] = hist[-1]["timestamp"]`.

- [ ] **Step 1: Write failing tests**

```python
def build(side, snap):
    return ExecutionControlV2.build_idempotency_key("u1", "BTC-USD", side, snap)

def test_idempotency_key_differs_per_decision_candle():
    snap = {"selected": {"strategy": "trend"}, "decision_candle_close": "2026-11-26T12:00:00+00:00"}
    later = {**snap, "decision_candle_close": "2026-11-26T12:01:00+00:00"}
    assert build("BUY", snap) != build("BUY", later)
    assert build("BUY", snap) == build("BUY", dict(snap))

def test_idempotency_key_requires_decision_time():
    with pytest.raises(ValueError):
        ExecutionControlV2.build_idempotency_key("u1", "BTC-USD", "BUY", {"selected": {"strategy": "t"}})

async def test_bot_can_buy_again_after_round_trip():
    # FakeDB + simulated market; force planner output BUY, SELL, BUY on three successive candle closes
    ...
    assert [t["side"] for t in db.trades_v2.docs if t["status"] == "filled"] == ["BUY", "SELL", "BUY"]
```

Update the existing key assertions in `test_phase3_trading_controls.py` and `test_e2e_paper_smoke.py`. Add `decision_candle_close` to their contexts and assert the new format. The e2e replay check still expects one trade.

- [ ] **Step 2: Run and confirm the failures**

Run: `cd backend && python -m pytest tests/test_bot_engine_v2.py tests/test_phase3_trading_controls.py -v`
Expected: `test_bot_can_buy_again_after_round_trip` FAILS with only `["BUY", "SELL"]` filled, because the second BUY is skipped as a duplicate.

- [ ] **Step 3: Implement the key change and set `decision_candle_close` in `process_symbol`**

- [ ] **Step 4: Run the tests**

Run: `cd backend && python -m pytest tests -q`
Expected: PASS, including the e2e smoke test.

- [ ] **Step 5: Commit**

```bash
git add backend/services/execution_control_v2.py backend/services/bot_engine.py backend/tests
git commit -m "fix(execution): key idempotency on the decision candle so the bot can trade again (P0-1)"
```

### Task 7: Daily loss resets at UTC midnight (P0-4)

**Files:**
- Modify: `backend/services/portfolio_service_v2.py` (`ensure_account_state`, `update_risk_snapshot`)
- Test: `backend/tests/test_phase2_portfolio_accounting.py`

**Interfaces:**
- Produces: `portfolio_state.daily_start_date` holds the ISO date in UTC. `update_risk_snapshot(user_id, price_map=None, now: datetime | None = None)` rolls `daily_start_equity` to the current total equity when `daily_start_date` differs from `now`'s UTC date, or is missing. It writes both fields, and the snapshot's `daily_pnl` uses the rolled value.

- [ ] **Step 1: Write failing tests**

```python
async def test_daily_start_equity_rolls_at_utc_midnight():
    # state: cash 9,000, daily_start_equity 10,000, daily_start_date "2026-11-25"
    snap = await svc.update_risk_snapshot("u1", now=datetime(2026, 11, 26, 0, 0, 5, tzinfo=UTC))
    assert snap["daily_pnl"] == 0.0
    state = await db.portfolio_state.find_one({"user_id": "u1"})
    assert (state["daily_start_equity"], state["daily_start_date"]) == (9000.0, "2026-11-26")

async def test_daily_start_equity_holds_within_the_day():
    # same state, daily_start_date "2026-11-26"
    snap = await svc.update_risk_snapshot("u1", now=datetime(2026, 11, 26, 23, 59, tzinfo=UTC))
    assert snap["daily_pnl"] == -1000.0

async def test_restart_days_later_rolls_once():
    # daily_start_date "2026-11-20", equity 8,000 now
    snap = await svc.update_risk_snapshot("u1", now=datetime(2026, 11, 26, 9, 0, tzinfo=UTC))
    assert snap["daily_pnl"] == 0.0
```

- [ ] **Step 2: Run and confirm the failures**

Run: `cd backend && python -m pytest tests/test_phase2_portfolio_accounting.py -v`
Expected: the roll tests FAIL with `daily_pnl == -1000.0` and `-2000.0`.

- [ ] **Step 3: Implement**

`ensure_account_state` also sets `daily_start_date`. `update_risk_snapshot` rolls as described. `RiskGuardV2.portfolio_metrics` reads the state after `BotEngine.cycle` has called `update_risk_snapshot`, so it sees the rolled value. Keep that call order.

- [ ] **Step 4: Run the tests**

Run: `cd backend && python -m pytest tests/test_phase2_portfolio_accounting.py tests/test_bot_engine_v2.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/services/portfolio_service_v2.py backend/tests/test_phase2_portfolio_accounting.py
git commit -m "fix(risk): roll daily start equity at the UTC day boundary (P0-4)"
```

### Task 8: A regime filter that can say "range" (P0-13)

**Files:**
- Modify: `backend/services/regime_service_v2.py`
- Test: create `backend/tests/test_regime_service_v2.py`

**Interfaces:**
- Produces: `RegimeServiceV2.classify(prices) -> "unknown" | "range" | "trend_up" | "trend_down"`. It uses the 20-bar efficiency ratio `ER = |p[-1] - p[-21]| / sum(|p[i] - p[i-1]|)` over the last 20 changes. When `ER < 0.30` it returns `range`. Otherwise it returns `trend_up` or `trend_down` by the sign of `p[-1] - p[-21]`. It returns `unknown` below 30 prices, as today. `mixed` is removed, and nothing in `signal_planner_v2.py` reads it.

- [ ] **Step 1: Write failing tests**

```python
def test_choppy_series_is_range():
    prices = [100 + (1 if i % 2 else -1) for i in range(60)]
    assert RegimeServiceV2().classify(prices) == "range"

def test_steady_rise_is_trend_up():
    assert RegimeServiceV2().classify([100 + i for i in range(60)]) == "trend_up"

def test_steady_fall_is_trend_down():
    assert RegimeServiceV2().classify([200 - i for i in range(60)]) == "trend_down"

def test_random_walks_produce_range_sometimes():
    rng = random.Random(7)
    seen = set()
    for _ in range(500):
        p = [100.0]
        for _ in range(59):
            p.append(p[-1] * (1 + rng.uniform(-0.01, 0.01)))
        seen.add(RegimeServiceV2().classify(p))
    assert {"range", "trend_up", "trend_down"} <= seen
```

- [ ] **Step 2: Run and confirm the failures**

Run: `cd backend && python -m pytest tests/test_regime_service_v2.py -v`
Expected: the choppy-series and random-walk tests FAIL, because "range" needs `sma10 == sma30` exactly.

- [ ] **Step 3: Implement**

- [ ] **Step 4: Run the tests**

Run: `cd backend && python -m pytest tests/test_regime_service_v2.py tests/test_strategy_signals_v2.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/services/regime_service_v2.py backend/tests/test_regime_service_v2.py
git commit -m "fix(signals): efficiency-ratio regime so the range filter runs (P0-13)"
```

### Task 9: Clip oversized orders and keep one drawdown limit (P0-14, P0-15)

**Files:**
- Modify: `backend/services/risk_guard_v2.py`
- Modify: `backend/services/bot_engine.py` (`process_symbol` uses the clipped notional)
- Modify: `backend/services/portfolio_service_v2.py` (`equity_floor` in `update_risk_snapshot`)
- Modify: `backend/auth_core.py:110`, `backend/api_routes_v2.py:19`
- Modify: `backend/tests/test_phase3_trading_controls.py:106-116`
- Test: `backend/tests/test_phase3_trading_controls.py`

**Interfaces:**
- Produces: `can_open_position(...)` returns `{"allowed": True, "reason": "ok", "notional": min(proposed, max_position_notional)}`. The exposure check uses the clipped value. The "position notional exceeds limit" rejection is removed.
- Produces: `RiskGuardV2` drops `capital_floor_pct`. `capital_floor_value` in the metrics is `equity_high * (1 - max_drawdown_pct)`, for display only. `should_kill_switch` no longer has a separate capital-floor reason, so drawdown is the one limit.
- Produces: `update_risk_snapshot(..., max_drawdown_pct: float = 0.05)` sets `equity_floor = max_equity * (1 - max_drawdown_pct)`. `BotEngine.cycle` passes `risk.max_drawdown_pct`.

- [ ] **Step 1: Write failing tests**

```python
def test_oversized_buy_is_clipped_not_rejected():
    guard = RiskGuardV2(max_position_notional=100)
    metrics = guard.portfolio_metrics({"cash_balance": 10000.0}, [], {})
    result = guard.can_open_position(metrics, [], proposed_notional=140.0)
    assert result == {"allowed": True, "reason": "ok", "notional": 100.0}

def test_drawdown_limit_is_the_only_equity_halt():
    guard = RiskGuardV2(max_drawdown_pct=0.05)
    m = guard.portfolio_metrics({"cash_balance": 9650.0, "equity_high": 10000.0, "daily_start_equity": 9650.0}, [], {})
    assert guard.should_kill_switch(m)["triggered"] is False   # 3.5% down: old floor halted here
    m = guard.portfolio_metrics({"cash_balance": 9500.0, "equity_high": 10000.0, "daily_start_equity": 9500.0}, [], {})
    assert "drawdown" in guard.should_kill_switch(m)["reason"]
```

Replace `test_risk_guard_enforces_capital_floor_and_position_limits`. The capital-floor behavior it asserted is the bug. Its position-count and exposure assertions move into the new tests unchanged. Record this in the PR body.

- [ ] **Step 2: Run and confirm the failures**

Run: `cd backend && python -m pytest tests/test_phase3_trading_controls.py -v`
Expected: both FAIL. The first is rejected with "position notional exceeds limit". The second is halted by "capital floor breached" at 3.5%.

- [ ] **Step 3: Implement**

Remove `capital_floor` from the signup default config and from the `BotConfig` model. Stored configs that still hold the field are ignored, because `BotConfig` already sets `extra="ignore"`.

- [ ] **Step 4: Run the tests**

Run: `cd backend && python -m pytest tests -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend
git commit -m "fix(risk): clip to the position cap and use one drawdown limit (P0-14, P0-15)"
```

### Task 10: Portfolio heat in dollars (P0-16)

**Files:**
- Modify: `backend/services/advanced_risk_manager.py:35-80`
- Test: create `backend/tests/test_advanced_risk_manager.py`

**Interfaces:**
- Consumes: marked positions from `PortfolioServiceV2.mark_positions`, with `quantity`, `current_price` and `avg_price`.
- Produces: `calculate_portfolio_heat` risk per position is `quantity * (current_price or avg_price) * 0.03`. The return keys are unchanged.

- [ ] **Step 1: Write the failing test**

```python
def test_heat_counts_dollars_at_risk():
    heat = AdvancedRiskManager().calculate_portfolio_heat(
        [{"quantity": 0.002, "current_price": 50000.0, "avg_price": 49000.0}], total_equity=10000.0)
    assert heat["total_heat"] == 3.0          # 0.002 * 50,000 * 3%
    assert heat["heat_percent"] == 0.03
```

- [ ] **Step 2: Run and confirm the failure**

Run: `cd backend && python -m pytest tests/test_advanced_risk_manager.py -v`
Expected: FAIL with `total_heat == 0.0`, because 0.002 times 0.03 rounds to 0.

- [ ] **Step 3: Implement**

- [ ] **Step 4: Run the test**

Run: `cd backend && python -m pytest tests/test_advanced_risk_manager.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/services/advanced_risk_manager.py backend/tests/test_advanced_risk_manager.py
git commit -m "fix(risk): portfolio heat uses position value, not units (P0-16)"
```

### Task 11: Paper trading works over a simulated day

**Files:**
- Test: create `backend/tests/test_paper_soak_v2.py`

**Interfaces:**
- Consumes: `BotEngine(db, market=...)` (Task 4), the moving simulator with `clock` (Task 2), and every fix above.

- [ ] **Step 1: Write the test**

The test uses the real CI MongoDB through `app_state.db`, with a unique user ID and cleanup, as `test_e2e_paper_smoke.py` does. It creates an active bot config for `BTC-USD` and `ETH-USD`. It steps a mutable clock from `NOON` in 60-second increments for 1,440 steps and calls `engine.cycle(user_id)` each step. The engine's paper adapter shares the clocked simulator through the Task 4 wiring. Set `cooldown_seconds` to 300 as today.

```python
filled = [t for t in trades if t["status"] == "filled"]
assert sum(t["side"] == "BUY" for t in filled) >= 2
assert sum(t["side"] == "SELL" for t in filled) >= 1
assert len({t["idempotency_key"] for t in filled}) == len(filled)
assert (await db.bot_configs.find_one({"user_id": user_id}))["is_active"] is True
report = await LedgerServiceV2(db).reconcile(user_id, starting_cash=10000.0)
assert report["status"] == "ok", report
```

If the strategies make fewer than two buys in a day on the simulator, lengthen the run to two days. Do not loosen the thresholds. Record the counts the test saw in the PR body.

- [ ] **Step 2: Run it**

Run: `cd backend && python -m pytest tests/test_paper_soak_v2.py -v`
Expected: PASS in CI. On `main` before this milestone it fails: at most one BUY per strategy, and no movement.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_paper_soak_v2.py
git commit -m "test: paper bot trades repeatedly over a simulated day"
```

### Task 12: Pull request

- [ ] **Step 1:** Push `feat/m15a-paper-trading` and open a PR to `main` titled "Milestone 1.5a: make paper trading work". The body lists each P0 ID with its commit and failing-first test, the replaced capital-floor test and why, and the soak counts.
- [ ] **Step 2:** Run `gh pr checks`. Expected: every CI job passes. Fix failures at the root cause.
- [ ] **Step 3:** Leave the merge to the owner.
