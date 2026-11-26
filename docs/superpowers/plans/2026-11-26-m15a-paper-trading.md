# Milestone 1.5a: Make Paper Trading Work Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the engine bugs that stop the paper bot after a few trades (roadmap P0-1, 3, 4, 9, 13, 14, 15, 16), so paper trading runs on live public prices and keeps trading.

**Architecture:** Small, test-first fixes inside the existing services. One new setting, `MARKET_DATA_SOURCE`, replaces `SIMULATION_MODE`. The simulator becomes a function of time, kept for tests and CI. No new services and no change to the live gate path.

**Tech Stack:** Python 3.12, FastAPI, Motor/MongoDB, aiohttp, pytest with pytest-asyncio (session loop, see `backend/tests/conftest.py`).

**Spec:** `docs/superpowers/specs/2026-11-24-premium-upgrade-design.md`, plus the owner's "Autonomous Trading Bot — Full-Potential Roadmap (1)" (Nov 25, 2026), P0 table and "Decided" notes. The decisions this plan relies on are copied into Global Constraints.

**Status:** Approved by owner on 2026-11-26 with six changes, which are folded in below. Execution is subagent-driven.

## Process: prove each test fails first, in CI

The phone cannot run the backend suite, so CI is the only test runner.

1. Commit the new or changed tests alone. Push, then wait for that commit's CI run to finish with `gh run watch <id> --exit-status`.
2. Confirm with `gh run view <id> --log-failed` that the new tests fail for the reason the task states, and that nothing else broke.
3. Commit the fix. Its message ends with `Failing first: <run URL>`. Push and wait for a green run.
4. Never push the fix while the test-only run is in progress. CI's concurrency group cancels the earlier run, which would erase the proof.
5. The task reviewer opens the failing run, checks the failure reason, and checks that the fix commit's run is green.

The pull request is a draft that stacks on PR #1. Do not merge it. If PR #1 is squash-merged, rebase with `git rebase --onto main feat/m1-foundation feat/m15a-paper-trading`.

## Global Constraints

- Branch from `main` after PR #1 merges. If PR #1 was squash-merged, run `git rebase --onto origin/main feat/m1-foundation feat/m15a-paper-trading`.
- Scope is P0-1, 3, 4, 9, 13, 14, 15 and 16 only. The other 14 P0 bugs belong to M1.5b.
- Each fix starts with a failing test that reproduces the bug. Commit the test with the fix.
- `MARKET_DATA_SOURCE` takes exactly `live`, `simulated` or `replay`. It replaces `SIMULATION_MODE`. When only `SIMULATION_MODE` is set, true maps to `simulated` and false maps to `live`, with a deprecation warning. `MARKET_DATA_SOURCE` wins when both are set.
- `.env.example` sets `MARKET_DATA_SOURCE=live`. The CI compose job sets it to `simulated`, because it copies `.env.example`.
- Tests and CI never call Coinbase. They use `simulated`.
- Store every candle the bot sees, so a paper week can be replayed.
- Paper fills price from the exchange's current bid and ask, not last trade plus a fixed allowance. Any extra slippage is a small optional setting that defaults to 0. Simulated mode uses a synthetic spread. Quotes stay behind the market-data interface, so M3's ccxt adapter can supply them.
- Do not weaken the kill switch, the live gate or any pre-submit check. Do not touch the live adapters.
- CI on GitHub (Python 3.12) is the source of truth. The owner's phone cannot run the backend suite.

## Review Focus

1. **Exchange unreachable in live mode.** One symbol's candles fail. The bot should skip that symbol, alert, and still process the others. Test in Task 4.
2. **Old `.env` files.** `SIMULATION_MODE=True` is set and `MARKET_DATA_SOURCE` is not. Settings should load, log a deprecation warning naming `MARKET_DATA_SOURCE`, and use `simulated`. Test in Task 1.
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
- Produces: `MarketDataService(db=None, source: str | None = None, seed=None, clock=None, session_factory=None, timeout_seconds: float | None = None)`. `source=None` resolves through `resolve_market_data_source()`. The attribute `self.source` replaces `self.simulation_mode`. `clock` and `session_factory` are used in Tasks 2 and 3.
- Produces: `resolve_market_data_source(environ=os.environ) -> str` in `market_data_service.py`. If `MARKET_DATA_SOURCE` is set, it returns `parse_market_data_source` of it. Otherwise, if `SIMULATION_MODE` is set, it logs a deprecation warning and maps true (`1`, `true`, `yes`, `on`) to `simulated` and anything else to `live`. Otherwise it returns `live`. Settings and the market service both call it, so there is one rule.
- Produces: `SettingsV2.market_data_source: str`, set from `resolve_market_data_source()`. It replaces `simulation_mode`.

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

@pytest.mark.parametrize("legacy,expected", [("True", "simulated"), ("false", "live")])
def test_legacy_simulation_mode_maps_with_deprecation_warning(monkeypatch, caplog, legacy, expected):
    monkeypatch.delenv("MARKET_DATA_SOURCE", raising=False)
    monkeypatch.setenv("SIMULATION_MODE", legacy)
    assert SettingsV2.from_env().market_data_source == expected
    assert "deprecated" in caplog.text and "MARKET_DATA_SOURCE" in caplog.text

def test_market_data_source_wins_over_legacy(monkeypatch):
    monkeypatch.setenv("SIMULATION_MODE", "True")
    monkeypatch.setenv("MARKET_DATA_SOURCE", "live")
    assert SettingsV2.from_env().market_data_source == "live"

# test_phase1_market_data.py
def test_market_data_service_reads_source_from_env(monkeypatch):
    monkeypatch.setenv("MARKET_DATA_SOURCE", "replay")
    assert MarketDataService().source == "replay"
```

Use the same env setup as the existing tests in `test_settings_v2.py`, which already provide a valid `JWT_SECRET` and `DEBUG`.

- [ ] **Step 2: Run them and confirm they fail**

Run: `cd backend && python -m pytest tests/test_settings_v2.py tests/test_phase1_market_data.py -v`
Expected: the new tests FAIL with an `AttributeError` on `market_data_source` or `source`.

- [ ] **Step 3: Implement**

- Add the constants and `parse_market_data_source` to `market_data_service.py`. Change the constructor to the signature above. Every `if self.simulation_mode:` becomes `if self.source == "simulated":`.
- In `SettingsV2`, replace `simulation_mode` with `market_data_source`. `from_env` calls `resolve_market_data_source()`. Update `redacted_report` and `runtime_config.SIMULATION_MODE` (rename to `MARKET_DATA_SOURCE`).
- In `operational_readiness_v2.py`, the `paper_mode_simulation` check becomes `market_data_source_live`. It passes when the source is `live` and warns otherwise, with the message "Paper results only reflect the market when MARKET_DATA_SOURCE=live."
- At the top of `backend/tests/conftest.py`, add `os.environ.setdefault("MARKET_DATA_SOURCE", "simulated")`. In the six listed test files, replace `SIMULATION_MODE` with `MARKET_DATA_SOURCE` and the value `simulated`. Replace `simulation_mode=True` with `source="simulated"` in `test_phase1_market_data.py`.
- In `ci.yml`, change the job env to `MARKET_DATA_SOURCE: simulated`. In compose-smoke, after `cp .env.example .env`, add `sed -i 's/^MARKET_DATA_SOURCE=.*/MARKET_DATA_SOURCE=simulated/' .env`.
- Set `MARKET_DATA_SOURCE=live` in both `.env.example` files. Update the README table row and `docs/CONFIGURATION.md` to describe the three values.

- [ ] **Step 4: Run the full backend suite**

Run: `cd backend && python -m pytest tests -q`
Expected: all tests pass. `grep -rn SIMULATION_MODE backend .github .env.example docs README.md` finds only `resolve_market_data_source`, its tests, and one README line saying the variable is deprecated.

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
- Produces: `persist_candles(candles)` writes only candles newer than the latest stored `open_time` for that symbol, exchange and timeframe. It sends them in one `bulk_write([...UpdateOne(filter, {"$set": candle}, upsert=True)], ordered=False)` and does nothing when no candle is new. The filter keys are the same as today.
- Produces: with `source="replay"`, `get_candles` returns `get_stored_candles(symbol, timeframe, periods)` and the ticker is the last stored close. Both raise `MarketDataUnavailable` when nothing is stored. A time-travelling replay clock is left for the M4 drift monitor.

- [ ] **Step 1: Write failing tests**

`test_bot_engine_v2.py` holds an in-memory `FakeDB` for this and later tasks. Copy the `FakeCollection` from `test_phase2_portfolio_accounting.py`. Add `find`, `find_one_and_update`, `delete_one`, `update_many` and on-demand collections through `__getattr__`, as in `test_phase7_operational_readiness.py`.

```python
async def test_bot_persists_candles_it_reads():
    db = FakeDB()
    engine = BotEngine(db, market=MarketDataService(db=db, source="simulated", clock=lambda: NOON))
    await engine.process_symbol("u1", "BTC-USD", [], {}, RiskGuardV2(), {"BTC-USD": 45000.0})
    assert len(db.market_candles.docs) == 120

async def test_only_new_closed_candles_are_written_in_one_bulk_call():
    db = FakeDB()  # its market_candles records each bulk_write call and its ordered flag
    clock = {"now": NOON}
    svc = MarketDataService(db=db, source="simulated", clock=lambda: clock["now"])
    await svc.get_candles("BTC-USD", periods=120)
    clock["now"] = NOON + timedelta(minutes=1)
    await svc.get_candles("BTC-USD", periods=120)
    calls = db.market_candles.bulk_calls
    assert [len(ops) for ops, _ in calls] == [120, 1]
    assert all(ordered is False for _, ordered in calls)

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

Accept an injected `market` and default to `MarketDataService(db=db)`. Pass it to the paper adapter as described above. Implement `persist_candles` as described and the `replay` branches. In `cycle`, wrap each `process_symbol` call: on `MarketDataUnavailable`, log, emit the same `market_data_failed` alert that `get_price_map` emits, and continue. `stop()` awaits `self.market.close()`.

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
- Produces: `_reference_quote(symbol) -> dict` with `price`, `bid` and `ask`. It reads them through the market service's `get_current_price`, the interface M3's ccxt adapter will implement. If the quote has no `bid` or `ask`, as in `replay`, it synthesizes them as `price` plus and minus half of `PAPER_FALLBACK_SPREAD_BPS` (default 2).
- Produces: `PaperExecutionConfig.extra_slippage_bps`, from `PAPER_EXTRA_SLIPPAGE_BPS`, default 0. It replaces `buy_slippage_bps` and `sell_slippage_bps` and their env vars. Order results add `"price_basis": "quote" | "synthetic_spread"`.

- [ ] **Step 1: Write failing tests**

```python
async def test_paper_buy_fills_at_ask():
    adapter = PaperExecutionAdapterV2(market_data=FakeQuote(price=100.0, bid=99.9, ask=100.1))
    order = await adapter.place_market_buy("BTC-USD", 50.0)
    assert order["filled_price"] == 100.1 and order["price_basis"] == "quote"

async def test_paper_sell_fills_at_bid():
    ...
    assert order["filled_price"] == 99.9 and order["price_basis"] == "quote"

async def test_paper_fill_uses_synthetic_spread_without_quote():
    adapter = PaperExecutionAdapterV2(market_data=FakeQuote(price=100.0, bid=None, ask=None))
    order = await adapter.place_market_buy("BTC-USD", 50.0)
    assert order["filled_price"] == pytest.approx(100.0 * 1.0001)
    assert order["price_basis"] == "synthetic_spread"

async def test_extra_slippage_is_optional_and_small():
    config = PaperExecutionAdapterV2.from_env()
    assert config.extra_slippage_bps == 0.0
    adapter = PaperExecutionAdapterV2(market_data=FakeQuote(price=100.0, bid=99.9, ask=100.1),
                                      config=replace(config, extra_slippage_bps=1.0))
    assert (await adapter.place_market_buy("BTC-USD", 50.0))["filled_price"] == pytest.approx(100.1 * 1.0001)
```

- [ ] **Step 2: Run and confirm the failures**

Run: `cd backend && python -m pytest tests/test_phase3_paper_execution_engine.py -v`
Expected: the first two FAIL because the fill is last price plus 8 bps.

- [ ] **Step 3: Implement**

Buys fill at `ask` and sells at `bid`, times `1 ± extra_slippage_bps / 10000`. Fees, minimums and partial fills are unchanged. Existing tests that assert the old 8 bps fill price change to the new basis. List each one in the PR body.

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
- Produces: `build_idempotency_key(user_id, symbol, side, signal_snapshot) -> str` returns `f"{user_id}:{symbol}:{side}:{strategy}:{timeframe}:{decision_candle_close}"`. `timeframe` comes from `signal_snapshot["timeframe"]` and is required, like the decision time. `decision_candle_close` is the ISO close time of the last closed candle the plan was built from, read from `signal_snapshot["decision_candle_close"]`. If it is missing, raise `ValueError("signal_snapshot.decision_candle_close is required")`, so no order is placed without a decision time.
- Produces: `process_symbol` sets `plan["timeframe"]` (today `"1m"`, passed to `get_historical_data`) and `plan["decision_candle_close"] = hist[-1]["timestamp"]`.
- Produces: `already_executed(key)` matches `{"idempotency_key": key, "status": {"$ne": "rejected"}}`. Every status except `rejected` counts as executed.
- Produces: `record_trade_attempt` sets `idempotency_claim: True` on every trade whose status is not `rejected`. `mongo_indexes_v2.py` adds a unique index `uniq_trade_idempotency_claim` on `trades_v2.idempotency_key` with `partialFilterExpression={"idempotency_claim": True}`. MongoDB partial indexes cannot filter with `$ne`, hence the flag. When the insert raises `DuplicateKeyError`, the engine logs `duplicate_order_skipped` and stops for that order.
- Modify also: `backend/services/portfolio_service_v2.py` (`record_trade_attempt`), `backend/services/mongo_indexes_v2.py`, `backend/tests/test_phase9_mongo_index_validation.py`.

- [ ] **Step 1: Write failing tests**

```python
def build(side, snap):
    return ExecutionControlV2.build_idempotency_key("u1", "BTC-USD", side, snap)

def test_idempotency_key_differs_per_decision_candle():
    snap = {"selected": {"strategy": "trend"}, "timeframe": "1m", "decision_candle_close": "2026-11-26T12:00:00+00:00"}
    later = {**snap, "decision_candle_close": "2026-11-26T12:01:00+00:00"}
    assert build("BUY", snap) != build("BUY", later)
    assert build("BUY", snap) == build("BUY", dict(snap))

def test_idempotency_key_requires_decision_time():
    with pytest.raises(ValueError):
        ExecutionControlV2.build_idempotency_key("u1", "BTC-USD", "BUY", {"selected": {"strategy": "t"}})

def test_idempotency_key_includes_timeframe():
    snap = {"selected": {"strategy": "trend"}, "timeframe": "1m", "decision_candle_close": "2026-11-26T12:00:00+00:00"}
    assert build("BUY", snap) != build("BUY", {**snap, "timeframe": "1h"})

@pytest.mark.parametrize("status,expected", [("filled", True), ("partially_filled", True), ("unknown", True), ("rejected", False)])
async def test_any_non_rejected_attempt_counts_as_executed(status, expected):
    db = FakeDB()
    await db.trades_v2.insert_one({"idempotency_key": "k", "status": status})
    assert await ExecutionControlV2(db).already_executed("k") is expected

async def test_bot_can_buy_again_after_round_trip():
    # FakeDB + simulated market; force planner output BUY, SELL, BUY on three successive candle closes
    ...
    assert [t["side"] for t in db.trades_v2.docs if t["status"] == "filled"] == ["BUY", "SELL", "BUY"]
```

Add to `test_phase9_mongo_index_validation.py` an assertion that `uniq_trade_idempotency_claim` is created unique with that partial filter. Add to `test_e2e_paper_smoke.py`, which runs on real MongoDB, a direct insert of a second non-rejected trade with the same key, expecting `DuplicateKeyError`.

Update the existing key assertions in `test_phase3_trading_controls.py` and `test_e2e_paper_smoke.py`. Add `timeframe` and `decision_candle_close` to their contexts and assert the new format. The e2e replay check still expects one trade.

- [ ] **Step 2: Run and confirm the failures**

Run: `cd backend && python -m pytest tests/test_bot_engine_v2.py tests/test_phase3_trading_controls.py -v`
Expected: `test_bot_can_buy_again_after_round_trip` FAILS with only `["BUY", "SELL"]` filled, because the second BUY is skipped as a duplicate. The timeframe, status and index tests also fail.

- [ ] **Step 3: Implement the key, the status rule, the claim flag and the index as described**

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
- Modify: `backend/auth_core.py:110` (stop writing `capital_floor` for new users), `backend/api_routes_v2.py:19` (keep the field, accepted and ignored)
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

Stop writing `capital_floor` in the signup default config. Keep `capital_floor: float | None = None` on `BotConfig`, with a comment that it is deprecated and ignored, so older clients can still post it. `RiskGuardV2.from_config` ignores it, and stored configs that hold it keep loading. The frontend shows `equity_floor`, which now comes from the drawdown limit, so it needs no change.

Add `test_legacy_capital_floor_is_accepted_and_ignored`: `RiskGuardV2.from_config({"capital_floor": 0.99, "max_drawdown_pct": 0.05})` does not halt at 2% down, and `BotConfig(capital_floor=0.97)` validates.

The PR body must say: "The equity halt moves from 3% below peak (the old 0.97 capital floor) to 5% below peak (the drawdown limit)."

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

### Task 11: Paper trading works across midnight on a scripted market

**Files:**
- Test: create `backend/tests/test_paper_soak_v2.py`

**Interfaces:**
- Consumes: `BotEngine(db, market=...)` and the paper adapter wiring (Task 4), bid and ask quotes (Task 5), and every fix above.
- Produces: a test-only `ScriptedMarket` that implements the market-service methods the engine and paper adapter call: `get_current_price`, `get_historical_data`, `get_candles` and `close`. It reads a mutable clock. Its price at time `t` comes from a scripted path that rises, falls, then rises again, with a fixed spread for `bid` and `ask`. It has no randomness.

- [ ] **Step 1: Write the test**

Use the real CI MongoDB through `app_state.db`, with a unique user ID and cleanup, as `test_e2e_paper_smoke.py` does. Create an active bot config for `BTC-USD` only, with `cooldown_seconds` at 300. Start the clock at 22:00 UTC and advance it 60 seconds per step, calling `engine.cycle(user_id)` directly each step. Never call `start()` or sleep. Run until 02:00 UTC the next day, so the run crosses midnight. Shape the path, for example up 22:00–23:00, down 23:00–00:30, up 00:30–02:00, so that the bot buys, sells and buys again. Check the strategies' thresholds in `strategy_ensemble_v2.py` when sizing the moves.

```python
filled = [t for t in trades if t["status"] == "filled"]
buys = [t for t in filled if t["side"] == "BUY"]
assert len(buys) >= 2
assert any(t["side"] == "SELL" for t in filled)
# P0-1: a second buy of the same symbol and strategy
assert len({(t["symbol"], t["signal_snapshot"]["selected"]["strategy"]) for t in buys}) < len(buys)
assert len({t["idempotency_key"] for t in filled}) == len(filled)
# P0-4: the daily start rolled at midnight and the bot did not halt
state = await db.portfolio_state.find_one({"user_id": user_id})
assert state["daily_start_date"] == "2026-11-27"
assert (await db.bot_configs.find_one({"user_id": user_id}))["is_active"] is True
assert (await LedgerServiceV2(db).reconcile(user_id, starting_cash=10000.0))["status"] == "ok"
```

- [ ] **Step 2: Prove the test catches the bugs**

The fixes are already in place, so the test passes on this branch. To show it would have caught P0-1 and P0-4, push one throwaway commit that restores the old idempotency key (no timeframe or decision time) and stops the daily roll. Confirm in CI that this test fails on the second-buy and `daily_start_date` assertions. Then push `git revert` of that commit and confirm CI is green. Put both run URLs in the PR body.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_paper_soak_v2.py
git commit -m "test: paper bot buys, sells and buys again across UTC midnight"
```

### Task 12: Pull request

- [ ] **Step 1:** The draft PR already exists. Update its body to list each P0 ID with its fix commit and failing-first run URL, the replaced capital-floor test and why, the 3% to 5% halt change, the paper fill tests whose expected prices changed, and the Task 11 proof runs.
- [ ] **Step 2:** Run `gh pr checks`. Expected: every CI job passes. Fix failures at the root cause.
- [ ] **Step 3:** Leave the PR in draft until PR #1 merges, then rebase onto `main` and mark it ready. Leave the merge to the owner.
