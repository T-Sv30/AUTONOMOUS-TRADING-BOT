# Configuration and Redacted Settings Report

> This is for educational/backtesting/paper-trading use only. Live trading involves substantial risk of loss. Not financial advice.

The backend now centralizes runtime configuration in `services/settings_v2.py`.

## Goals

- Validate unsafe production settings at startup.
- Keep compatibility with existing `runtime_config.py` imports.
- Expose a redacted diagnostics report through readiness responses.
- Prevent raw secrets from appearing in readiness payloads or logs.

## Typed settings

`SettingsV2` reads and validates:

- runtime role
- trading mode
- simulation mode
- MongoDB connection config
- CORS origins
- JWT secret strength
- ops admin allowlist
- Coinbase readonly/live credentials
- manual live-trading gates
- live-order kill switch

## Redacted report

`SettingsV2.redacted_report()` reports whether secrets are configured and their lengths, but never returns raw values.

Example shape:

```json
{
  "debug": false,
  "trading_mode": "paper",
  "runtime_role": "api",
  "jwt_secret": {"configured": true, "length": 48, "redacted": "***"},
  "coinbase": {
    "api_key": {"configured": false, "length": 0, "redacted": ""}
  },
  "live_trading": {
    "enabled": false,
    "coinbase_live_order_kill_switch": true
  }
}
```

## Readiness

`/readyz` and `/api/ops/readiness` now include the redacted settings report inside the environment block.

Use strict readiness before production deploys:

```bash
curl http://localhost:8000/readyz?strict=true
```

## Environment variable compatibility

Coinbase variables must use the names consumed by the adapter:

```bash
COINBASE_EXCHANGE_API_KEY=
COINBASE_EXCHANGE_API_SECRET=
COINBASE_EXCHANGE_PASSPHRASE=
```

The older shorthand names are not used by the current adapter.

## Production-safe defaults

For API-only production role:

```bash
DEBUG=False
RUNTIME_ROLE=api
API_EMBED_BOT_MANAGER=false
RUN_MONGO_INDEX_BOOTSTRAP=false
TRADING_MODE=paper
MARKET_DATA_SOURCE=live
COINBASE_LIVE_ORDER_KILL_SWITCH=True
```

For worker role:

```bash
RUNTIME_ROLE=worker
RUN_MONGO_INDEX_BOOTSTRAP=false
TRADING_MODE=paper
MARKET_DATA_SOURCE=live
```

`MARKET_DATA_SOURCE` accepts `live` (real Coinbase prices, default), `simulated` (deterministic fake prices for tests and CI) or `replay`. The older `SIMULATION_MODE` is deprecated; when only it is set, `True` maps to `simulated` and anything else to `live`, with a warning.

`MARKET_DATA_REPLAY_EXCHANGE` (default `coinbase_exchange`) picks which stored candles `replay` reads, since simulated and exchange candles can share one collection. Use `simulation` to replay candles stored by the simulated source.
