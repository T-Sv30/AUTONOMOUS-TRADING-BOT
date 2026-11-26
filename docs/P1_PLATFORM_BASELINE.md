# P1 Platform Baseline

This document describes the first productionization batch for the Autonomous Trading Bot.

> This is for educational/backtesting/paper-trading use only. Live trading involves substantial risk of loss. Not financial advice.

## What this baseline adds

- GitHub Actions CI for backend tests, backend security checks, and frontend build.
- Backend development tooling: pytest coverage, ruff, bandit, pip-audit.
- Root `.env.example` with safe paper-trading defaults.
- `Makefile` operator commands.
- Backend Dockerfile.
- Frontend Dockerfile and nginx runtime config.
- Docker Compose stack with MongoDB, index bootstrap, API, worker, and frontend services.
- Runtime roles for production-style process separation.

## Runtime roles

The backend supports explicit roles through `RUNTIME_ROLE`:

| Role | Purpose |
|---|---|
| `api` | FastAPI HTTP server only; does not embed bot manager by default |
| `worker` | Dedicated BotManager process for autonomous paper bot cycles |
| `indexes` | One-shot Mongo index bootstrap command |
| `all` | Legacy/local combined role for API + embedded bot manager |

Important flags:

| Variable | Default | Purpose |
|---|---:|---|
| `API_EMBED_BOT_MANAGER` | `True` in debug/all, false in production API | Controls whether API lifespan starts BotManager |
| `RUN_MONGO_INDEX_BOOTSTRAP` | `True` in debug | Controls whether API lifespan creates indexes |

Production API deployments should set:

```bash
RUNTIME_ROLE=api
API_EMBED_BOT_MANAGER=false
RUN_MONGO_INDEX_BOOTSTRAP=false
```

Dedicated worker deployments should set:

```bash
RUNTIME_ROLE=worker
RUN_MONGO_INDEX_BOOTSTRAP=false
```

## Local quickstart

```bash
cp .env.example .env
make dev-up
```

Then open:

- Frontend: http://localhost:3000
- Backend health: http://localhost:8000/healthz
- Backend readiness: http://localhost:8000/readyz

## Backend commands

```bash
make setup-backend
make test-backend
make lint-backend
make audit-backend
make indexes
make run-backend
make run-worker
```

## Frontend commands

```bash
make setup-frontend
make build-frontend
```

## CI gates

The CI workflow has three jobs:

1. `backend-tests`
   - Installs backend runtime and dev dependencies.
   - Runs pytest with coverage.
   - Enforces a starting coverage floor of 65%.

2. `backend-security`
   - Runs ruff.
   - Runs bandit.
   - Runs pip-audit.

3. `frontend-build`
   - Installs frontend dependencies.
   - Builds the React app.

## Safety posture

The Docker Compose stack preserves the current safety boundary:

- `TRADING_MODE=paper`
- `MARKET_DATA_SOURCE=live`
- `COINBASE_LIVE_ORDER_KILL_SWITCH=True`
- live trading disabled by default
- API and worker are separate services
- index bootstrap is explicit and one-shot

The autonomous bot path remains paper-only.

## Next productionization slices

1. Typed settings with redacted configuration report.
2. Scoped authorization and MFA foundation.
3. Signed live approval challenge.
4. Live-readonly adapter hardening.
5. Worker heartbeat and leader election.
