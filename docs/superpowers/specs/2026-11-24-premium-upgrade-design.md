# Premium Upgrade Design

Date: 2026-11-24
Status: approved by owner 2026-11-24 (exchanges: Coinbase Advanced Trade and Kraken)

## Intent

Turn AUTONOMOUS-TRADING-BOT from a gated research platform into a serious, premium crypto trading bot. It is for the owner and the people the owner invites. Each person trades their own exchange account for their own gain. "Best in class" here means: strategies you can trust because the backtests are honest, many exchanges, strong risk control, and control from your phone, all without weakening the existing safety gates.

The owner chose to upgrade this codebase in place (over running Freqtrade underneath, or a rewrite) because the gate, audit and ledger design is the project's strongest asset.

## Goals

1. A verified foundation: every test runs in CI on every pull request, and dead or duplicate code is gone.
2. Invite-only accounts. The owner is admin. Each member connects their own exchange keys, stored encrypted.
3. Many exchanges through one adapter built on the ccxt library, for both market data and orders.
4. One strategy plug-in interface. The same strategy code runs in backtest, paper and live.
5. An honest backtester: fees, slippage, next-bar fills (no lookahead), walk-forward testing, parameter optimization, and comparison with buy-and-hold.
6. Premium risk and execution: volatility-based position sizing, stop-loss, take-profit and trailing stops, daily-loss and drawdown halts, limit orders with fallback.
7. Telegram alerts and commands, including status, pause and kill.
8. A dashboard covering the strategy lab, live positions and P&L, keys and settings, and admin invites.

## Non-goals

- No pooled money. The bot never holds or moves funds between users.
- No billing, subscriptions or public sign-up.
- No high-frequency trading or market making.
- No native mobile app in this round. Telegram and the web dashboard cover the phone.
- No claims that a model predicts prices. ML may rank or filter signals later; it is not in this round.

## Anti-goals (failure even if it works)

- Anything that weakens the kill switch, the live-trading gate, or the paper-to-live stages.
- Backtests that look good because of lookahead, missing fees or survivorship.
- Exchange keys stored or logged in plain text.
- Accepting exchange keys that can withdraw funds.
- Live autonomous trading switched on by default for anyone.

## Constraints

- The owner's phone cannot run the backend test suite: pinned numpy and pydantic-core fail to build on Termux Python 3.14. CI on GitHub (Python 3.12) is the source of truth for tests.
- Keep FastAPI, MongoDB and React. Keep the existing gate services (`live_trading_gate_v2`, kill switch env, approval challenges, audit).
- Work lands as small pull requests, one per milestone, on branches from `main`. Nothing is merged without the owner.
- Deleting the ~76 remote branches is irreversible and needs the owner's explicit approval first.

## Approaches considered

| Approach | Trade-off | Decision |
|---|---|---|
| Upgrade in place | Most work; every feature fits the existing gates and audit trail | **Chosen by owner** |
| Freqtrade underneath, this repo as a layer | Fastest proven features; two systems to keep in sync, existing execution and ledger mostly unused | Rejected |
| Rewrite from scratch | Throws away working safety design | Rejected |

## Current state (found 2026-11-24)

- Bot path is paper only: `BotManager -> BotEngine -> ExecutionServiceV2 -> TradingServiceV2 -> PaperExecutionAdapterV2`. Manual live orders go through `LiveTradingServiceV2 -> LiveTradingGateV2 -> CoinbaseLiveExecutionAdapterV2`.
- Registration is open to anyone (`auth_core.py`). There are no roles or invites.
- Exchange keys are global environment variables, not per user.
- Coinbase only, using the Exchange API (`api.exchange.coinbase.com`), which retail accounts may not have. Retail uses Advanced Trade.
- Strategies: a three-part ensemble (trend, mean reversion, breakout) plus a feature-scoring signal. No plug-in interface.
- The backtester already has fees, slippage and a walk-forward mode. It needs a lookahead audit, more metrics and optimization.
- Unused modules with no importers: `bot_engine_legacy`, `backtesting_engine`, `advanced_order_manager`. Several v1 services are still imported.
- Committed `__pycache__` files.
- No recorded backend CI run. Backend CI triggers only on pushes to `main` that touch `backend/`.
- The dashboard has about five real screens on a large unused component kit.

## Design

### Milestone 1: Foundation

- CI runs backend and frontend tests on every pull request and on manual dispatch. Fix whatever fails.
- Remove `__pycache__` files and add them to `.gitignore`.
- Delete modules with no importers. Migrate the remaining v1 imports to v2 services and delete the v1 files.
- Produce a list of stale remote branches for the owner to approve before any deletion.

### Milestone 2: Accounts and keys

- Roles: `admin` and `member`. The first registered user, or one named by env, is admin.
- Registration requires a single-use invite code that an admin creates, with an expiry. Admins can suspend members.
- Per-user exchange credentials, encrypted at rest with Fernet. The master key comes from env and never reaches Mongo or logs. API responses show only the last four characters.
- On connect, check the key's permissions where the exchange allows it, and reject keys with withdrawal rights.
- Each user gets their own risk limits, bots and audit trail. All queries are scoped by user ID; tests prove one user cannot read another's data.

### Milestone 3: Exchanges via ccxt

- An `ExchangeAdapter` interface: fetch candles, ticker, balances, open orders; create and cancel orders.
- A ccxt-backed implementation (async ccxt). The paper adapter implements the same interface using live market data.
- The live adapter is reachable only through `LiveTradingGateV2` and keeps the kill switch.
- First exchanges: Coinbase Advanced Trade and Kraken. Others are configuration, gated by an allowlist.
- Market data moves from the hand-written Coinbase client to the adapter, with the simulator kept for tests.

### Milestone 4: Strategy and research engine

- A `Strategy` base class: a typed parameter schema, a warm-up length, and `on_bar(history) -> Signal`. Signals carry side, strength and an optional stop and target.
- Port the three ensemble strategies and the feature-scoring strategy to plug-ins. Add a DCA strategy and a grid strategy.
- The backtester fills at the next bar's open, applies fees and slippage, and is tested against a known lookahead trap.
- Metrics: total and annualized return, Sharpe, Sortino, max drawdown, profit factor, win rate, exposure, trade count, and buy-and-hold over the same period.
- Walk-forward and parameter optimization (grid and random search) with results saved per user.
- The bot engine runs the same plug-in in paper and live.

### Milestone 5: Risk and execution

- Position size from risk per trade and ATR volatility, capped by max position percent.
- Stop-loss, take-profit and trailing stops, managed by the engine on every tick in paper and live.
- Per-user daily-loss stop and max-drawdown halt that pause all that user's bots and send an alert.
- Limit-order entries with a timeout, then a market fallback that respects a slippage cap.
- All of this runs before the existing live gate and pre-submit safety checks, never instead of them.

### Milestone 6: Telegram

- Each user links Telegram with a one-time code from the dashboard.
- Alerts: fills, stops hit, halts, errors, daily summary.
- Commands: `/status`, `/positions`, `/pnl`, `/pause`, `/resume`, `/kill`. Kill pauses that user's bots and cancels open orders. Commands only act on the linked user's own account.

### Milestone 7: Dashboard

- Strategy lab: pick a strategy and parameters, run a backtest, see the equity curve, trades and metrics against buy-and-hold.
- Live view: positions, open orders, P&L, bot status, risk usage.
- Settings: exchange keys, risk limits, Telegram link.
- Admin: invites, members, suspend.
- Remove unused UI kit components.

## Builder's calls (minor, decided by Claude)

- ccxt over hand-written adapters, because it covers 100+ exchanges with one API and is MIT licensed.
- Fernet for key encryption, from the `cryptography` package already pulled in by auth dependencies.
- Optimization starts with grid and random search; Optuna can come later.
- Charts in the dashboard use Recharts, a common React choice.
- Milestone order is as listed. Milestones 3 and 4 can overlap once 2 is merged.

## Testing

- Every milestone adds tests and must pass CI before its pull request is ready.
- Cross-user isolation tests for every user-scoped endpoint.
- Backtester tests with hand-computed expected results, plus the lookahead trap.
- Exchange adapter tests against recorded responses, never real keys in CI.
- Paper mode end-to-end smoke test on every pull request.

## Left to the builder

File layout within `backend/services`, exact endpoint shapes, Mongo collection names, and UI layout details, following existing conventions.
