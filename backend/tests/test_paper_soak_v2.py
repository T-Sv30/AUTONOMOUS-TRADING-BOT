"""Paper soak: the real bot engine on real MongoDB, on a scripted market across UTC midnight.

One run of 241 engine cycles (22:00 to 02:00 UTC, one per scripted minute) is shared by
the tests below, so a regression in trading again (P0-1), in the daily roll (P0-4) and a
halt each show up as their own failure.

The bot loses on a crash before midnight and on another crash after it. Each day's loss
stays under the daily-loss limit, but the two together cross it. So the bot stays active
only if the daily start equity rolls at midnight.
"""

import os
import uuid
from datetime import UTC, datetime, timedelta

os.environ.setdefault("DEBUG", "True")
os.environ.setdefault("JWT_SECRET", "test-secret-for-paper-soak-more-than-32-characters")
os.environ.setdefault("CORS_ORIGINS", "http://localhost:3000")
os.environ.setdefault("MARKET_DATA_SOURCE", "simulated")
os.environ.setdefault("TRADING_MODE", "paper")
os.environ.setdefault("RUNTIME_ROLE", "api")
os.environ.setdefault("API_EMBED_BOT_MANAGER", "false")
os.environ.setdefault("RUN_MONGO_INDEX_BOOTSTRAP", "false")
os.environ.setdefault("COINBASE_LIVE_ORDER_KILL_SWITCH", "true")
os.environ.setdefault("LIVE_TRADING_ENABLED", "false")
os.environ.setdefault("LIVE_EXECUTION_ADAPTER", "disabled")

import pytest
import pytest_asyncio

from app_state import db
from services.bot_engine import BotEngine
from services.ledger_service_v2 import LedgerServiceV2
from services.mongo_indexes_v2 import MongoIndexServiceV2
from services.portfolio_service_v2 import PortfolioServiceV2

START = datetime(2026, 11, 26, 22, 0, tzinfo=UTC)
END = datetime(2026, 11, 27, 2, 0, tzinfo=UTC)
STEP = timedelta(seconds=60)
BASE_PRICE = 45000.0
HALF_SPREAD_USD = 5.0
WIGGLE = 0.0002  # +/-2 bps alternating each minute, so volatility features are never zero
# (minute after 22:00, level vs BASE_PRICE), linear between knots. A 0.3% pop to a 20-bar high
# (breakout buy) then an 8% crash (sell at a loss): at 22:31/22:32 and again at 00:21/00:22.
PATH_KNOTS = [(0, 0.0), (30, 0.0), (31, 0.003), (32, -0.08), (140, -0.08), (141, -0.077), (142, -0.16), (240, -0.16)]
# Each crash loses about 7 bps of equity on a ~$75 position; both together about 14 bps.
MAX_DAILY_LOSS = 0.001


def scripted_price(at: datetime) -> float:
    minute = int((at - START).total_seconds() // 60)
    level = PATH_KNOTS[0][1] if minute <= PATH_KNOTS[0][0] else PATH_KNOTS[-1][1]
    for (m0, l0), (m1, l1) in zip(PATH_KNOTS, PATH_KNOTS[1:], strict=False):
        if m0 < minute <= m1:
            level = l0 + (l1 - l0) * (minute - m0) / (m1 - m0)
            break
    wiggle = WIGGLE if minute % 2 else -WIGGLE
    return round(BASE_PRICE * (1 + level + wiggle), 2)


class ScriptedMarket:
    """Deterministic market on a mutable clock. The ticker and the candles read one price path."""

    def __init__(self, now: datetime):
        self.now = now

    def clock(self) -> datetime:
        return self.now

    async def get_current_price(self, symbol: str) -> dict:
        price = scripted_price(self.now)
        return {
            "symbol": symbol,
            "price": price,
            "bid": round(price - HALF_SPREAD_USD, 2),
            "ask": round(price + HALF_SPREAD_USD, 2),
            "timestamp": self.now.isoformat(),
            "simulation": True,
            "source": "scripted",
            "quality": "scripted",
        }

    async def get_candles(self, symbol: str, timeframe: str = "1m", periods: int = 100) -> list[dict]:
        assert timeframe == "1m", timeframe
        end = self.now.replace(second=0, microsecond=0)  # closed candles only, ending at the clock's minute boundary
        candles = []
        for idx in range(periods):
            open_time = end - STEP * (periods - idx)
            close_time = open_time + STEP
            open_price, close_price = scripted_price(open_time), scripted_price(close_time)
            candles.append({
                "symbol": symbol,
                "exchange": "scripted",
                "timeframe": timeframe,
                "granularity_seconds": 60,
                "open_time": open_time.isoformat(),
                "close_time": close_time.isoformat(),
                "open": open_price,
                "high": max(open_price, close_price),
                "low": min(open_price, close_price),
                "close": close_price,
                "price": close_price,
                "volume": 500.0,
                "source": "scripted",
                "simulation": True,
                "quality": "scripted",
                "is_stale": False,
            })
        return candles

    async def get_historical_data(self, symbol: str, periods: int = 100, timeframe: str = "1m") -> list[dict]:
        return [
            {
                "timestamp": candle["close_time"],
                "price": candle["close"],
                "volume": candle["volume"],
                "simulation": candle["simulation"],
                "source": candle["source"],
                "timeframe": candle["timeframe"],
                "quality": candle["quality"],
            }
            for candle in await self.get_candles(symbol, timeframe=timeframe, periods=periods)
        ]

    async def close(self) -> None:
        return None


async def cleanup_user(user_id: str) -> None:
    for collection_name in [
        "bot_configs",
        "portfolio_state",
        "positions_v2",
        "trades_v2",
        "ledger_entries",
        "reconciliation_reports",
        "risk_metrics",
        "execution_locks",
        "alerts",
    ]:
        await getattr(db, collection_name).delete_many({"user_id": user_id})


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def soak(request):
    await MongoIndexServiceV2(db).ensure_indexes()
    user_id = f"paper-soak-{uuid.uuid4().hex}"
    try:
        await PortfolioServiceV2(db).ensure_account_state(user_id)
        # The account opens on the scripted day, so the run starts from a known daily_start_date whatever the real date is.
        await db.portfolio_state.update_one({"user_id": user_id}, {"$set": {"daily_start_date": START.date().isoformat()}})
        await db.bot_configs.insert_one({
            "user_id": user_id,
            "is_active": True,
            "symbols": ["BTC-USD"],
            "cooldown_seconds": 300,
            "max_daily_loss": MAX_DAILY_LOSS,
        })

        market = ScriptedMarket(START)
        engine = BotEngine(db, market=market)
        midnight = START.replace(hour=0) + timedelta(days=1)
        first_snapshot_after_midnight = None
        while market.now <= END:
            snapshots_before = await db.risk_metrics.count_documents({"user_id": user_id})
            await engine.cycle(user_id)
            if market.now == midnight:
                new_snapshots = await db.risk_metrics.find({"user_id": user_id}, {"_id": 0}).sort("_id", 1).skip(snapshots_before).limit(1).to_list(1)
                first_snapshot_after_midnight = new_snapshots[0] if new_snapshots else None  # None: no cycle ran at midnight
            market.now += STEP

        trades = await db.trades_v2.find({"user_id": user_id}, {"_id": 0}).sort("created_at", 1).to_list(1000)
        summary = [(t["side"], t["status"], (t.get("signal_snapshot") or {}).get("decision_candle_close"), ((t.get("signal_snapshot") or {}).get("selected") or {}).get("strategy")) for t in trades]
        reporter = request.config.pluginmanager.get_plugin("terminalreporter")
        config = await db.bot_configs.find_one({"user_id": user_id}, {"_id": 0})
        if reporter:
            reporter.write_line(f"paper soak trades ({len(trades)}): {summary}")
            reporter.write_line(
                f"paper soak first snapshot after midnight {first_snapshot_after_midnight}, "
                f"is_active {config.get('is_active')}, halt_reason {config.get('halt_reason')!r}"
            )
        yield {"user_id": user_id, "trades": trades, "first_snapshot_after_midnight": first_snapshot_after_midnight}
    finally:
        await cleanup_user(user_id)


async def test_paper_bot_buys_sells_and_buys_again_across_midnight(soak):
    trades = soak["trades"]
    filled = [t for t in trades if t["status"] == "filled"]
    buys = [t for t in filled if t["side"] == "BUY"]
    assert len(buys) >= 2
    assert any(t["side"] == "SELL" for t in filled)
    # P0-1: a second buy of the same symbol and strategy
    assert len({(t["symbol"], t["signal_snapshot"]["selected"]["strategy"]) for t in buys}) < len(buys)
    assert len({t["idempotency_key"] for t in filled}) == len(filled)


async def test_daily_start_rolls_at_midnight(soak):
    user_id = soak["user_id"]
    assert soak["first_snapshot_after_midnight"] is not None
    midnight_equity = soak["first_snapshot_after_midnight"]["total_equity"]
    # P0-4: the daily start rolled at midnight, date and equity
    state = await db.portfolio_state.find_one({"user_id": user_id})
    assert state["daily_start_date"] == "2026-11-27"
    assert midnight_equity < 10000.0  # the pre-midnight loss makes a stale baseline visible
    assert state["daily_start_equity"] == pytest.approx(midnight_equity)


async def test_bot_does_not_halt_across_midnight(soak):
    user_id = soak["user_id"]
    # P0-4: with the baseline rolled, the two days' losses are judged separately and the bot did not halt
    assert (await db.bot_configs.find_one({"user_id": user_id}))["is_active"] is True
    assert (await LedgerServiceV2(db).reconcile(user_id, starting_cash=10000.0))["status"] == "ok"
