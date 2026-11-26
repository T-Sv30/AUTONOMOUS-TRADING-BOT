# E2E Paper Smoke Test

> This is for educational/backtesting/paper-trading use only. Live trading involves substantial risk of loss. Not financial advice.

The E2E paper smoke test validates the minimum production candidate invariant for paper-mode operation:

```text
API auth/config -> controlled paper execution -> trade -> position -> ledger -> reconciliation -> idempotency replay guard
```

## Test file

```text
backend/tests/test_e2e_paper_smoke.py
```

## What it verifies

1. Backend health endpoint responds.
2. User can sign up through the API.
3. Authenticated dashboard/config endpoints work.
4. Bot can be activated through the API.
5. A controlled paper buy executes through `BotEngine.execute_buy`.
6. The resulting trade is paper/simulation only.
7. A position is created.
8. Ledger entries are created.
9. Ledger reconciliation reports `ok`.
10. Replaying the same idempotency context through a new `BotEngine` does not duplicate the trade or ledger entries.
11. Bot can be stopped through the API.

## Why the test invokes one controlled execution directly

The test intentionally does not wait on the background worker loop. Worker sleeps and market-signal timing would make CI slow and non-deterministic. Instead, the test uses the same execution path the worker uses after planning a buy:

```text
BotEngine.execute_buy -> ExecutionServiceV2 -> TradingServiceV2 -> PaperExecutionAdapterV2 -> PortfolioServiceV2 -> LedgerServiceV2
```

This keeps the smoke test fast, deterministic, and focused on production-critical paper execution invariants.

## Running locally

Start MongoDB locally, then run:

```bash
cd backend
DEBUG=True \
JWT_SECRET=test-secret-for-e2e-paper-smoke-more-than-32-chars \
CORS_ORIGINS=http://localhost:3000 \
MARKET_DATA_SOURCE=simulated \
TRADING_MODE=paper \
MONGO_URL=mongodb://localhost:27017 \
python -m pytest tests/test_e2e_paper_smoke.py
```

Or run the full backend suite:

```bash
make test-backend
```

## CI behavior

The test is automatically included in the `backend-tests` CI job because the workflow runs:

```bash
python -m pytest tests
```
