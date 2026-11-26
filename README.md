# Autonomous Trading Bot

A full-stack cryptocurrency trading research platform with paper trading, market analysis, risk-management scaffolding, ledger/reconciliation controls, live-readonly tooling, manually gated live-trading workflows, dashboard operator review pages, autonomous shadow-mode evaluation, canary controls, and a staged production-readiness architecture from Phase 0 through Phase 19.

This project is designed to answer a serious question:

Can a trading bot be built with enough gates, evidence, audit trails, dry runs, reconciliation, operator review, and operational controls that it can move from paper trading toward controlled live trading without skipping safety steps?

The repository now contains the architecture for that path.

It is important to understand the distinction:

- The codebase is production-readiness-framework complete.
- A real deployment is not automatically production-live approved until the gates are executed and satisfied with real evidence.

This is for educational, backtesting, paper-trading, and controlled systems-research use only. Live trading involves substantial risk of loss. Not financial advice.

---

## Table of Contents

1. What this project is
2. What this project is not
3. Beginner explanation
4. Current project status
5. Major features
6. How the system is organized
7. Installation path A: Docker, recommended for beginners
8. Installation path B: manual backend/frontend setup
9. Environment variables explained
10. How to use the app after startup
11. Trading modes explained
12. Live-readonly, manual-live, shadow, and canary explained
13. Completed roadmap, Phase 0 through Phase 19
14. Testing and validation
15. Production-readiness evidence checklist
16. Troubleshooting
17. Project documentation map
18. Safety rules
19. Final status

---

## 1. What This Project Is

This is a full-stack trading-bot research platform.

It includes:

- A Python/FastAPI backend.
- A React frontend dashboard.
- MongoDB persistence.
- JWT authentication.
- Paper/simulation trading support.
- Risk-management services.
- Ledger and reconciliation services.
- Live-readonly exchange observation tools.
- Manually gated live-trading workflow controls.
- Pilot review/report/signoff workflows.
- Production operations readiness gates.
- Autonomous shadow-mode evaluation.
- Autonomous canary eligibility gates.
- Final production-live release gate.

The project is no longer just a simple prototype. It now has a staged readiness architecture that separates each level of maturity:

- paper trading;
- live-readonly observation;
- dry-run rehearsal;
- tiny manual pilot;
- limited manual live release;
- autonomous design review;
- autonomous shadow mode;
- autonomous canary review;
- final production release review.

---

## 2. What This Project Is Not

This project is not a magic money machine.

It is not a guarantee of profit.

It is not automatically approved for real-money trading just because the code exists.

It is not currently a system you should blindly connect to an exchange with full permissions.

It does not mean autonomous live trading should be turned on immediately.

The correct interpretation is:

The repo now contains the tools, services, tests, and documentation needed to evaluate readiness. Actual readiness still requires running the checks, producing evidence, reviewing results, and passing the release gates.

---

## 3. Beginner Explanation

If you are new to trading software, think of this project like a flight simulator plus a pre-flight checklist system.

A normal trading bot might simply do this:

1. Look at market data.
2. Decide to buy or sell.
3. Send an order to an exchange.

That is dangerous if the system is not tested.

This project instead adds many safety layers:

1. First it trades only on paper.
2. Then it learns how to observe the real exchange without trading.
3. Then it rehearses a trade without submitting it.
4. Then it allows only a tiny manually approved pilot trade.
5. Then it requires reconciliation, reporting, and signoff.
6. Then it can evaluate whether limited manual live trading is safe.
7. Then, separately, it evaluates autonomous decisions in shadow mode without placing orders.
8. Then it defines a tiny autonomous canary gate.
9. Then it defines a final production release gate.

In plain English:

The project is built to prove readiness step by step instead of trusting the bot too early.

---

## 4. Current Project Status

| Area | Status |
|---|---|
| Paper/simulation trading | Supported |
| Dashboard/operator UI | Supported |
| Backend API | Supported |
| MongoDB persistence | Supported |
| Ledger and reconciliation foundation | Implemented |
| Live-readonly exchange observation | Implemented behind controls |
| Manual live pilot workflow | Gated and controlled |
| Controlled manual live release gate | Implemented |
| Autonomous live design gate | Implemented |
| Autonomous shadow mode | Implemented as no-order evaluation |
| Autonomous canary controls | Implemented as eligibility/review gates |
| Final production-live release gate | Implemented |
| Late-stage readiness tests in CI | Implemented |
| Actual production-live approval | Requires real environment evidence |

The most accurate label for the repo today is:

Production-readiness framework complete.

The most accurate label is not:

Production-live approved.

---

## 5. Major Features

### Paper and Simulation Trading

Paper trading means the bot behaves as if it is trading, but it does not use real money.

The project supports:

- simulated trading workflows;
- paper execution adapter;
- modeled fills, costs, slippage, minimums, rejections, and partial fills;
- portfolio state updates;
- trade history;
- UI validation without real funds.

### Market and Strategy Analysis

The bot includes scaffolding for:

- market regime analysis;
- signal generation;
- confidence scoring;
- allocation decisions;
- backtesting;
- walk-forward validation.

### Risk Management

The project includes risk-control logic for:

- max position notional;
- total exposure;
- open-position count;
- daily loss;
- drawdown;
- cooldowns;
- tiny pilot limits;
- manual approval gates;
- signed approval gates;
- kill-switch posture.

### Ledger and Reconciliation

A trading system needs to know whether its internal records match the exchange.

This project includes:

- ledger entries;
- portfolio state rebuilds;
- reconciliation reports;
- live-readonly comparison flows;
- post-submit reconciliation requirements;
- pilot reports;
- operator signoffs.

### Live-Readonly Observation

Live-readonly means the system can observe real exchange information without placing orders.

This can include:

- balances;
- orders;
- fills;
- account snapshots;
- reconciliation against internal records.

### Manual Live Pilot Workflow

The manual pilot workflow is for a tiny, human-approved, real-money test order after all gates pass.

It includes:

- pilot readiness checks;
- dry-run rehearsal;
- tiny order limits;
- symbol allowlists;
- manual approval;
- signed approval;
- post-order reconciliation;
- report generation;
- signoff;
- expansion-status review.

### Dashboard and Operator Review

The frontend includes:

- authentication;
- dashboard view;
- portfolio overview;
- trade and position views;
- risk/analysis views;
- authenticated pilot-review page at `/pilot-review`.

### Autonomous Shadow Mode

Shadow mode lets the autonomous logic make decisions against live-like conditions without placing orders.

It records:

- what it would have done;
- why it would have done it;
- risk decision evidence;
- simulated fill assumptions;
- simulated P/L evidence;
- halt assumptions;
- reconciliation assumptions;
- decision hashes.

### Autonomous Canary Controls

Canary mode is a future tiny autonomous test gate.

The service added in Phase 18 does not place orders. It only evaluates whether a canary candidate would be eligible.

### Final Production Release Gate

The final gate checks whether the whole project has enough evidence for production-live release review.

It requires:

- manual live readiness;
- shadow review;
- canary review;
- operations readiness;
- CI/security checks;
- monitoring;
- alerting;
- rollback;
- backup/restore;
- incident response;
- risk limits;
- release approval.

---

## 6. How the System Is Organized

At a high level:

- `backend/` contains the FastAPI app, services, tests, adapters, workers, and database logic.
- `frontend/` contains the React dashboard.
- `docs/` contains phase runbooks and production-readiness documents.
- `.github/workflows/ci.yml` contains CI validation.
- `docker-compose.yml` runs the local stack.
- `Makefile` provides shortcut commands.
- `.env.example` contains safe example configuration.

Important backend areas:

- API routes: backend API endpoints.
- Services: trading, risk, reconciliation, pilot, shadow, canary, release-gate logic.
- Tests: regression tests and phase-specific validation.
- Runtime config: environment-mode controls.

Important frontend areas:

- Dashboard components.
- Pilot review page.
- API client helpers.
- UI components.

---

## 7. Installation Path A: Docker Setup, Recommended for Beginners

This is the easiest way to run the whole project locally because Docker starts the backend, frontend, and MongoDB together.

### Step 1: Install required tools

Install:

- Git
- Docker Desktop, or Docker Engine plus Docker Compose
- Make, if available on your system

On Windows, Docker Desktop is usually the simplest option.

On macOS, Docker Desktop is usually the simplest option.

On Linux, Docker Engine and Docker Compose are usually preferred.

### Step 2: Clone the repository

Run:

    git clone https://github.com/SvilarT/AUTONOMOUS-TRADING-BOT.git
    cd AUTONOMOUS-TRADING-BOT

### Step 3: Create your local environment file

Run:

    cp .env.example .env

If your system supports Make, you can also run:

    make copy-env

### Step 4: Start the full local stack

Run:

    make dev-up

This uses Docker Compose to start:

- MongoDB database;
- FastAPI backend;
- React frontend.

### Step 5: Open the app

After the stack starts, open:

- Frontend: `http://localhost:3000`
- Backend health check: `http://localhost:8000/healthz`
- Backend readiness check: `http://localhost:8000/readyz`

### Step 6: Create an account

In the browser:

1. Go to `http://localhost:3000`.
2. Sign up with an email and password.
3. Log in.
4. Open the dashboard.

### Step 7: Stop the app when finished

Run:

    make dev-down

### Step 8: View logs if something breaks

Run:

    make logs

---

## 8. Installation Path B: Manual Backend and Frontend Setup

Use this path if you do not want Docker or if you are developing backend/frontend separately.

### Step 1: Install required tools

Install:

- Python 3.12 recommended
- Node.js 20 recommended
- npm
- MongoDB running locally
- Git

### Step 2: Clone the repository

Run:

    git clone https://github.com/SvilarT/AUTONOMOUS-TRADING-BOT.git
    cd AUTONOMOUS-TRADING-BOT

### Step 3: Create the environment file

Run:

    cp .env.example .env

For local manual backend use, make sure MongoDB points to localhost:

    MONGO_URL=mongodb://localhost:27017

### Step 4: Install backend dependencies

Run:

    make setup-backend

Equivalent manual command:

    cd backend
    python -m pip install --upgrade pip setuptools wheel
    python -m pip install -r requirements.txt -r requirements-dev.txt

### Step 5: Start the backend

From the repository root, run:

    make run-backend

Backend should start at:

    http://localhost:8000

Health check:

    http://localhost:8000/healthz

Readiness check:

    http://localhost:8000/readyz

### Step 6: Install frontend dependencies

In a second terminal, run:

    make setup-frontend

Equivalent manual command:

    cd frontend
    npm install --legacy-peer-deps

### Step 7: Start or build the frontend

For development, run from `frontend/`:

    npm start

For production build validation, run from the repository root:

    make build-frontend

Frontend runs at:

    http://localhost:3000

---

## 9. Environment Variables Explained

The project uses environment variables to control runtime behavior.

The safe default is paper mode.

### Safe local defaults

Use these values for normal local development:

    DEBUG=True
    MARKET_DATA_SOURCE=live
    TRADING_MODE=paper
    JWT_SECRET=replace-with-at-least-32-random-characters
    CORS_ORIGINS=http://localhost:3000
    MONGO_URL=mongodb://localhost:27017
    DB_NAME=trading_bot
    COINBASE_LIVE_ORDER_KILL_SWITCH=True
    LIVE_TRADING_ENABLED=False
    LIVE_EXECUTION_ADAPTER=disabled

### What each important variable means

| Variable | Beginner explanation |
|---|---|
| `DEBUG` | Turns development behavior on/off. Use `False` outside local development. |
| `MARKET_DATA_SOURCE` | Where prices come from: `live` (real Coinbase prices, the default), `simulated` (deterministic fake prices for tests and CI) or `replay` (serves the newest stored candles for `MARKET_DATA_REPLAY_EXCHANGE` and does not step through time yet, planned for M4). The old `SIMULATION_MODE` variable is deprecated: `True` maps to `simulated`, anything else to `live`. |
| `TRADING_MODE` | Controls the trading mode. Safe default is `paper`. |
| `JWT_SECRET` | Secret used for login tokens. Must be long and private. |
| `CORS_ORIGINS` | Frontend URLs allowed to talk to the backend. Do not use wildcard in production. |
| `MONGO_URL` | MongoDB connection string. |
| `DB_NAME` | MongoDB database name. |
| `COINBASE_EXCHANGE_API_KEY` | Exchange API key. Leave blank unless intentionally testing readonly/live workflows. |
| `COINBASE_EXCHANGE_API_SECRET` | Exchange API secret. Never commit this. |
| `COINBASE_EXCHANGE_PASSPHRASE` | Exchange API passphrase. Never commit this. |
| `LIVE_TRADING_ENABLED` | Must be false unless intentionally inside a gated live workflow. |
| `LIVE_EXECUTION_ADAPTER` | Should be `disabled` by default. |
| `LIVE_ALLOWED_SYMBOLS` | Symbols allowed for live workflows. |
| `LIVE_MAX_ORDER_NOTIONAL_USD` | Maximum notional size for live workflow orders. |
| `LIVE_MANUAL_APPROVAL_REQUIRED` | Requires human approval. Should stay true. |
| `LIVE_SIGNED_APPROVAL_REQUIRED` | Requires stronger signed approval. Should stay true. |
| `COINBASE_LIVE_ORDER_KILL_SWITCH` | Safety switch. Default should be true. |
| `REACT_APP_BACKEND_URL` | Frontend location of backend API. This is public frontend config, not a secret. |

### Never do this

Never commit real `.env` files.

Never put exchange secrets into frontend variables.

Never give an exchange API key withdrawal permission.

Never use real credentials in screenshots, issues, pull requests, logs, or chat messages.

---

## 10. How to Use the App After Startup

### Step 1: Open the frontend

Go to:

    http://localhost:3000

### Step 2: Create an account

Use the signup form.

Use a normal email and a strong password.

### Step 3: Log in

After login, you should see the dashboard.

### Step 4: Explore the dashboard

The dashboard is intended to show things like:

- portfolio information;
- P&L;
- positions;
- trades;
- risk information;
- analysis information;
- bot controls.

### Step 5: Use paper mode first

Do not start with live workflows.

Paper mode is the correct first mode because it does not use real money.

### Step 6: Open pilot review page when needed

The pilot review page is at:

    http://localhost:3000/pilot-review

It is for reviewing manual pilot readiness, reconciliation, pilot reports, signoffs, and expansion status.

---

## 11. Trading Modes Explained

### Paper mode

Paper mode means simulated trading.

No real exchange order should be placed by the autonomous bot path.

Use this for:

- normal development;
- testing;
- UI work;
- strategy work;
- risk-control work;
- beginner learning.

### Live-readonly mode

Live-readonly means the app can observe exchange data but should not trade.

Use this for:

- account snapshots;
- balances;
- orders/fills lookup;
- reconciliation.

### Manual live mode

Manual live mode means a human operator manually triggers a tiny gated order after many checks.

This is not the same as autonomous live trading.

### Controlled manual live mode

Controlled manual live means repeated manual trading may be considered only after the pilot, review, and operations gates pass.

It still requires:

- manual approval;
- signed approval;
- dry-run before each order;
- reconciliation after each order;
- report/signoff after each order.

### Autonomous shadow mode

Shadow mode means the autonomous system makes simulated decisions against live-like conditions without placing orders.

### Autonomous canary candidate mode

Canary candidate mode means the system checks whether a tiny autonomous canary could be considered.

The Phase 18 service does not submit orders.

### Production-live release mode

Production-live release means the final gate has passed with real evidence.

This is not automatically true just because the code exists.

---

## 12. Live-Readonly, Manual-Live, Shadow, and Canary Explained

### Live-readonly in plain English

The system can look at exchange data but not touch funds.

This is useful because it lets the app compare its internal records with the real exchange.

### Manual-live in plain English

A person approves a tiny order after the app checks many conditions.

The system then requires immediate review and reconciliation.

### Shadow mode in plain English

The bot says, “I would have bought here,” but does not actually buy.

This lets you study whether the autonomous strategy behaves sensibly before allowing real money.

### Canary in plain English

A canary is a tiny, tightly controlled real-world test.

The Phase 18 code does not execute the canary. It only checks whether a canary would be eligible for review.

---

## 13. Completed Roadmap

| Phase | Name | Status |
|---:|---|---:|
| 0 | Baseline safety and structure | Complete |
| 1 | Market data foundation | Complete |
| 2 | Backtesting engine | Complete |
| 3 | Paper execution engine | Complete |
| 4 | Ledger and reconciliation foundation | Complete |
| 4.5 | Manual live lifecycle wiring | Complete |
| 5 | Live-readonly and pilot readiness | Complete |
| 6 | Gated live execution workflow | Complete |
| 7 | Pilot review controls | Complete |
| 8 | Dashboard pilot review UI | Complete |
| 9 | Regression, CI, route, and frontend validation | Complete |
| 10 | Exchange account and secrets hardening | Complete |
| 11 | Full dry-run dress rehearsal validator | Complete |
| 12 | Tiny manual live pilot control layer | Complete |
| 13 | Pilot result review and limited manual release criteria | Complete |
| 14 | Production operations hardening | Complete |
| 15 | Controlled manual live release gate | Complete |
| 16 | Autonomous live gate design | Complete |
| 17 | Autonomous live shadow mode | Complete |
| 18 | Autonomous live canary controls | Complete |
| 19 | Production live trading release gate | Complete |
| Post-19 | Production readiness validation pass | Complete |

---

## 14. Testing and Validation

Testing matters because this project contains live-trading-related code paths, even though they are gated.

### Run all local CI-style checks

From the repository root:

    make ci-local

This runs backend tests, backend lint/security/audit checks, frontend lint/tests, and frontend build.

### Run backend tests

    make test-backend

### Run backend lint and security scan

    make lint-backend

### Run backend dependency audit

    make audit-backend

### Run frontend tests

    make test-frontend

### Build frontend

    make build-frontend

### Run late-stage readiness tests

    cd backend
    python -m pytest \
      tests/test_phase12_tiny_manual_live_pilot_control.py \
      tests/test_phase13_pilot_release_criteria.py \
      tests/test_phase14_operations_hardening.py \
      tests/test_phase15_manual_release_gate.py \
      tests/test_phase16_gate_design.py \
      tests/test_phase17_shadow_mode.py \
      tests/test_phase18_canary_controls.py \
      tests/test_phase19_production_release_gate.py \
      -q

### What CI checks

The GitHub Actions workflow checks:

- backend test suite;
- targeted backend validation;
- Phase 12 through Phase 19 readiness tests;
- backend lint;
- backend security scan;
- backend dependency audit;
- frontend lint;
- frontend tests;
- frontend build;
- backend Docker image build;
- frontend Docker image build;
- Docker Compose smoke test.

---

## 15. Production-Readiness Evidence Checklist

The project should not be called production-live ready until real evidence exists.

See the dedicated checklist:

    docs/PRODUCTION_READINESS_EVIDENCE_CHECKLIST.md

At a high level, production-live readiness requires:

1. CI passing on `main`.
2. Secrets and exchange credentials hardened.
3. Readonly exchange observation working.
4. Phase 11 dry-run rehearsal passing.
5. One tiny manual pilot completed.
6. Pilot reconciled, reported, and signed off.
7. Limited manual release gates passing.
8. Operations readiness passing.
9. Shadow mode evidence collected and reviewed.
10. Canary controls satisfied and reviewed.
11. Final Phase 19 release gate passing.
12. Explicit production release approval recorded.

---

## 16. Troubleshooting

### Problem: `make` command is not found

Your system may not have Make installed.

You can either install Make or run the commands manually.

For example, instead of:

    make setup-frontend

Run:

    cd frontend
    npm install --legacy-peer-deps

### Problem: Docker is not running

Make sure Docker Desktop or Docker Engine is started.

Then try:

    docker compose up --build

### Problem: frontend cannot reach backend

Check that backend is running at:

    http://localhost:8000/healthz

Check `.env` has:

    REACT_APP_BACKEND_URL=http://localhost:8000

### Problem: backend cannot reach MongoDB

If using Docker, the `.env.example` default uses:

    MONGO_URL=mongodb://mongo:27017

If running MongoDB manually on your machine, use:

    MONGO_URL=mongodb://localhost:27017

### Problem: tests fail because dependencies are missing

Run:

    make setup-backend
    make setup-frontend

### Problem: Git says you are not in a repository

Move into the project folder first:

    cd AUTONOMOUS-TRADING-BOT

### Problem: Termux on Android

If using Termux, first make sure you are inside the repo folder:

    cd ~/AUTONOMOUS-TRADING-BOT

Then check:

    git status

Docker may not be available in normal Termux environments, so manual commands are usually more realistic there.

---

## 17. Project Documentation Map

Core docs:

- `ARCHITECTURE.md`
- `PRODUCTION_ROADMAP.md`
- `docs/PRODUCTION_READINESS_EVIDENCE_CHECKLIST.md`

Phase docs:

- `docs/PHASE_8_DASHBOARD_PILOT_REVIEW_UI.md`
- `docs/PHASE_9_REGRESSION_CI_VALIDATION.md`
- `docs/PHASE_10_EXCHANGE_ACCOUNT_AND_SECRETS_HARDENING.md`
- `docs/PHASE_11_FULL_DRY_RUN_DRESS_REHEARSAL.md`
- `docs/PHASE_12_TINY_MANUAL_LIVE_PILOT_CONTROL.md`
- `docs/PHASE_13_PILOT_REVIEW_AND_LIMITED_MANUAL_RELEASE.md`
- `docs/PHASE_14_PRODUCTION_OPERATIONS_HARDENING.md`
- `docs/PHASE_15_CONTROLLED_MANUAL_LIVE_TRADING.md`
- `docs/PHASE_16_AUTONOMOUS_LIVE_GATE_DESIGN.md`
- `docs/PHASE_17_AUTONOMOUS_LIVE_SHADOW_MODE.md`
- `docs/PHASE_18_AUTONOMOUS_LIVE_CANARY_CONTROLS.md`
- `docs/PHASE_19_PRODUCTION_LIVE_TRADING_RELEASE_GATE.md`

---

## 18. Safety Rules

These rules matter:

1. Start in paper mode.
2. Do not use real exchange credentials until you understand the gates.
3. Never give API keys withdrawal permission.
4. Never put secrets in frontend variables.
5. Keep the kill switch closed by default.
6. Use readonly credentials before execution credentials.
7. Dry-run before any manual live pilot.
8. Keep first pilot tiny.
9. Reconcile after every live attempt.
10. Generate a report and signoff after every live attempt.
11. Do not enable autonomous live execution without shadow and canary evidence.
12. Do not call the system production-live ready until Phase 19 passes with real evidence.

---

## 19. Final Status

The repository has completed the planned Phase 0 through Phase 19 readiness architecture and now includes late-stage readiness tests in CI.

The next real-world milestone is not more architecture. It is evidence.

Recommended next sequence:

1. Wait for CI on `main` to pass.
2. Run the project locally in paper mode.
3. Confirm backend, frontend, and MongoDB operate correctly.
4. Run the full test suite.
5. Run the Phase 11 dry-run dress rehearsal in the actual environment.
6. Only after all gates pass, consider one tiny manual pilot.
7. Reconcile, report, sign off, and stop.
8. Continue toward Phase 19 only with real evidence.

Current final label:

Production-readiness framework complete; production-live approval still requires gate evidence.
