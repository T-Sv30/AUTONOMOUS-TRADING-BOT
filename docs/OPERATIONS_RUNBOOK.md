# Autonomous Trading Bot Operations Runbook

## Current production posture

Treat the system as live-capable but not automatically live-trading-ready. Default operation should remain paper mode until readiness checks pass and live gates are intentionally enabled.

## Safe default environment

```env
TRADING_MODE=paper
MARKET_DATA_SOURCE=live
LIVE_TRADING_ENABLED=false
LIVE_EXECUTION_ADAPTER=disabled
```

## Mode progression

1. Paper mode
2. Backtest validation
3. Paper execution validation
4. Ledger reconciliation
5. Live-readonly account reconciliation
6. Live-trading dry-run preview
7. Tiny manually approved live order

## Required checks before live-readonly

- Backend CI passes on `main`
- `/healthz` returns `ok`
- `/readyz` returns `ready` or only acceptable warnings
- `POST /api/ops/indexes/ensure` succeeds
- Coinbase readonly credentials are configured
- `TRADING_MODE=live-readonly`
- `LIVE_TRADING_ENABLED=false`

## Required checks before any non-dry-run live order

- `TRADING_MODE=live-trading`
- `LIVE_TRADING_ENABLED=true`
- `LIVE_EXECUTION_ADAPTER=coinbase_exchange_v2`
- User bot config includes `live_trading_enabled=true`
- `LIVE_APPROVAL_TOKEN` configured
- `LIVE_MAX_ORDER_NOTIONAL_USD` set to a very small amount, initially <= 25
- `POST /api/live-readonly/reconcile` returns `ok`
- `POST /api/live-trading/market-buy` with `dry_run=true` returns expected payload
- `GET /api/live-trading/audits` shows the dry-run audit

## Emergency halt

Use:

```text
POST /api/ops/emergency-halt
```

Payload:

```json
{
  "reason": "operator emergency halt"
}
```

Expected result:

- User bot configs are set inactive
- Halt reason is persisted
- Critical alert is emitted

## Live-order safety model

Actual live orders require all of the following:

- live-trading mode
- global live gate enabled
- Coinbase live adapter selected
- per-user live gate enabled
- symbol allowlist pass
- max notional pass
- approval token pass
- dry_run=false explicitly requested

If any check fails, the order is blocked and an audit record is written.

## Deployment checklist

- Confirm branch protection on `main`
- Confirm backend CI required before merge
- Configure secrets in deployment environment, not in Git
- Run indexes during startup or via `/api/ops/indexes/ensure`
- Keep initial deployment in paper mode
- Confirm logs for startup, index setup, and bot manager startup
- Run `/readyz?strict=true`
- Only then test live-readonly

## Rollback plan

1. Set `LIVE_TRADING_ENABLED=false`
2. Set `TRADING_MODE=paper` or `live-readonly`
3. Trigger `/api/ops/emergency-halt`
4. Rotate Coinbase credentials if live keys may be exposed
5. Reconcile ledger and exchange state
6. Review `live_order_audits`
