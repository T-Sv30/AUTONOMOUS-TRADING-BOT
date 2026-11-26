import hashlib
import logging
import math
import os
import random
from datetime import UTC, datetime, timedelta
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

MARKET_DATA_SOURCES = ("live", "simulated", "replay")
_TRUTHY = {"1", "true", "yes", "on"}


def parse_market_data_source(value: str | None) -> str:
    if value is None or not str(value).strip():
        return "live"
    source = str(value).strip().lower()
    if source not in MARKET_DATA_SOURCES:
        raise ValueError(
            f"Invalid MARKET_DATA_SOURCE={value!r}. Allowed values: {', '.join(MARKET_DATA_SOURCES)}"
        )
    return source


def resolve_market_data_source(environ=os.environ) -> str:
    """Single rule for the data source: MARKET_DATA_SOURCE, then legacy SIMULATION_MODE, then live."""
    configured = environ.get("MARKET_DATA_SOURCE")
    if configured is not None and configured.strip():
        return parse_market_data_source(configured)
    legacy = environ.get("SIMULATION_MODE")
    if legacy is not None and legacy.strip():
        logger.warning("SIMULATION_MODE is deprecated; set MARKET_DATA_SOURCE=live|simulated|replay instead.")
        return "simulated" if legacy.strip().lower() in _TRUTHY else "live"
    return "live"


class MarketDataUnavailable(RuntimeError):
    pass


class MarketDataService:
    """Market-data gateway for simulation and Coinbase Exchange OHLCV data.

    Phase 1 goals implemented here:
    - deterministic simulation data: a seeded function of time
    - real Coinbase candle retrieval for non-simulation mode
    - MongoDB candle persistence when a db handle is supplied
    - timeframe/granularity support
    - stale-data and data-quality metadata
    """

    TIMEFRAME_SECONDS = {
        "1m": 60,
        "5m": 300,
        "15m": 900,
        "1h": 3600,
        "6h": 21600,
        "1d": 86400,
    }
    MAX_COINBASE_CANDLES = 300

    def __init__(
        self,
        db=None,
        source: str | None = None,
        seed: int | str | None = None,
        clock=None,
        session_factory=None,
        timeout_seconds: float | None = None,
    ):
        self.db = db
        self.source = parse_market_data_source(source) if source is not None else resolve_market_data_source()
        self.clock = clock or self.utc_now
        self.session_factory = session_factory or aiohttp.ClientSession
        self.timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else float(os.getenv("MARKET_DATA_TIMEOUT_SECONDS", "10"))
        )
        self._session = None
        self.base_url = os.getenv("COINBASE_EXCHANGE_URL", "https://api.exchange.coinbase.com")
        self.seed = seed if seed is not None else os.getenv("SIMULATION_SEED", "autonomous-trading-bot")

    def _get_session(self):
        if self._session is None:
            self._session = self.session_factory(timeout=aiohttp.ClientTimeout(total=self.timeout_seconds))
        return self._session

    async def close(self) -> None:
        session, self._session = self._session, None
        if session is not None:
            await session.close()

    @classmethod
    def granularity_for(cls, timeframe: str) -> int:
        try:
            return cls.TIMEFRAME_SECONDS[timeframe]
        except KeyError as exc:
            allowed = ", ".join(sorted(cls.TIMEFRAME_SECONDS))
            raise ValueError(f"Unsupported timeframe={timeframe!r}. Allowed values: {allowed}") from exc

    @staticmethod
    def utc_now() -> datetime:
        return datetime.now(UTC)

    @staticmethod
    def parse_timestamp(value: str | datetime) -> datetime:
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=UTC)
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))

    @staticmethod
    def candle_checksum(candle: dict[str, Any]) -> str:
        parts = [
            candle.get("symbol"),
            candle.get("timeframe"),
            candle.get("open_time"),
            candle.get("open"),
            candle.get("high"),
            candle.get("low"),
            candle.get("close"),
            candle.get("volume"),
        ]
        return hashlib.sha256("|".join(map(str, parts)).encode("utf-8")).hexdigest()

    # Simulated log-price waves: (period seconds, amplitude). 6 hours, 2 days, 9 days.
    SIM_WAVES = ((6 * 3600, 0.015), (2 * 86400, 0.04), (9 * 86400, 0.08))
    SIM_MINUTE_NOISE = 0.0015
    SIM_WICK_NOISE = 0.002

    @staticmethod
    def _sim_base_price(symbol: str) -> float:
        return 45000.0 if "BTC" in symbol else 2500.0 if "ETH" in symbol else 1000.0

    @staticmethod
    def _seed_int(material: str) -> int:
        return int(hashlib.sha256(material.encode("utf-8")).hexdigest()[:16], 16)

    def _sim_price(self, symbol: str, at: datetime) -> float:
        """The one source of simulated prices: a deterministic function of (seed, symbol, at)."""
        epoch = at.timestamp()
        digest = hashlib.sha256(f"{self.seed}:{symbol}".encode()).digest()
        log_move = 0.0
        for idx, (period, amplitude) in enumerate(self.SIM_WAVES):
            phase = int.from_bytes(digest[idx * 4 : idx * 4 + 4], "big") / 2**32 * 2 * math.pi
            log_move += amplitude * math.sin(2 * math.pi * epoch / period + phase)
        minute = int(epoch // 60)
        noise = random.Random(self._seed_int(f"{self.seed}:{symbol}:{minute}")).uniform(-self.SIM_MINUTE_NOISE, self.SIM_MINUTE_NOISE)
        return self._sim_base_price(symbol) * math.exp(log_move) * (1 + noise)

    async def get_current_price(self, symbol: str) -> dict[str, Any]:
        if self.source == "simulated":
            return self._simulate_price_data(symbol)

        try:
            async with self._get_session().get(f"{self.base_url}/products/{symbol}/ticker") as response:
                if response.status != 200:
                    raise MarketDataUnavailable(f"Coinbase ticker returned HTTP {response.status} for {symbol}")
                data = await response.json()
                price = data.get("price")
                if price is None:
                    raise MarketDataUnavailable(f"Coinbase ticker response missing price for {symbol}")
                return {
                    "symbol": symbol,
                    "price": float(price),
                    "bid": float(data.get("bid") or price),
                    "ask": float(data.get("ask") or price),
                    "volume": float(data.get("volume", 0)),
                    "timestamp": self.clock().isoformat(),
                    "simulation": False,
                    "source": "coinbase_exchange",
                    "quality": "live_ticker",
                }
        except MarketDataUnavailable:
            raise
        except Exception as exc:
            logger.error("Error fetching market data: %s", exc)
            raise MarketDataUnavailable(f"Market data unavailable for {symbol}") from exc

    def _simulate_price_data(self, symbol: str) -> dict[str, Any]:
        now = self.clock()
        price = self._sim_price(symbol, now)
        half_spread = float(os.getenv("SIM_SPREAD_BPS", "2")) / 2 / 10000
        rng = random.Random(self._seed_int(f"{self.seed}:{symbol}:ticker:{int(now.timestamp() // 60)}"))
        return {
            "symbol": symbol,
            "price": round(price, 8),
            "bid": round(price * (1 - half_spread), 8),
            "ask": round(price * (1 + half_spread), 8),
            "volume": round(rng.uniform(100, 1000), 8),
            "change_24h": round((price / self._sim_price(symbol, now - timedelta(days=1)) - 1) * 100, 2),
            "timestamp": now.isoformat(),
            "simulation": True,
            "source": "seeded_simulation",
            "quality": "synthetic_seeded",
        }

    async def get_historical_data(self, symbol: str, periods: int = 100, timeframe: str = "1m") -> list[dict[str, Any]]:
        candles = await self.get_candles(symbol, timeframe=timeframe, periods=periods)
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
            for candle in candles
        ]

    async def get_candles(self, symbol: str, timeframe: str = "1m", periods: int = 100) -> list[dict[str, Any]]:
        if periods <= 0:
            return []
        self.granularity_for(timeframe)
        if self.source == "simulated":
            candles = self._simulate_candles(symbol, timeframe, periods)
        else:
            candles = await self._fetch_coinbase_candles(symbol, timeframe, periods)
        await self.persist_candles(candles)
        return candles

    def _simulate_candles(self, symbol: str, timeframe: str, periods: int) -> list[dict[str, Any]]:
        granularity = self.granularity_for(timeframe)
        now = self.clock()
        end_epoch = int(now.timestamp()) // granularity * granularity
        start = datetime.fromtimestamp(end_epoch - granularity * periods, tz=UTC)
        candles: list[dict[str, Any]] = []
        for idx in range(periods):
            open_time = start + timedelta(seconds=granularity * idx)
            close_time = open_time + timedelta(seconds=granularity)
            open_price = self._sim_price(symbol, open_time)
            close_price = self._sim_price(symbol, close_time)
            rng = random.Random(self._seed_int(f"{self.seed}:{symbol}:{timeframe}:{int(open_time.timestamp())}"))
            high = max(open_price, close_price) * (1 + rng.uniform(0, self.SIM_WICK_NOISE))
            low = min(open_price, close_price) * (1 - rng.uniform(0, self.SIM_WICK_NOISE))
            volume = rng.uniform(100, 1000)
            candle = {
                "symbol": symbol,
                "exchange": "simulation",
                "timeframe": timeframe,
                "granularity_seconds": granularity,
                "open_time": open_time.isoformat(),
                "close_time": close_time.isoformat(),
                "open": round(open_price, 8),
                "high": round(high, 8),
                "low": round(low, 8),
                "close": round(close_price, 8),
                "price": round(close_price, 8),
                "volume": round(volume, 8),
                "source": "seeded_simulation",
                "simulation": True,
                "quality": "synthetic_seeded",
                "is_stale": False,
                "ingested_at": now.isoformat(),
            }
            candle["checksum"] = self.candle_checksum(candle)
            candles.append(candle)
        return candles

    async def _fetch_coinbase_candles(self, symbol: str, timeframe: str, periods: int) -> list[dict[str, Any]]:
        granularity = self.granularity_for(timeframe)
        limit = min(periods + 1, self.MAX_COINBASE_CANDLES)  # +1: the newest candle is usually still open
        now = self.clock()
        end = now
        start = end - timedelta(seconds=granularity * limit)
        params = {"granularity": granularity, "start": start.isoformat(), "end": end.isoformat()}
        try:
            async with self._get_session().get(f"{self.base_url}/products/{symbol}/candles", params=params) as response:
                if response.status != 200:
                    raise MarketDataUnavailable(f"Coinbase candles returned HTTP {response.status} for {symbol}")
                raw = await response.json()
        except MarketDataUnavailable:
            raise
        except Exception as exc:
            logger.error("Error fetching Coinbase candles: %s", exc)
            raise MarketDataUnavailable(f"Historical market data unavailable for {symbol}") from exc

        if not isinstance(raw, list) or not raw:
            raise MarketDataUnavailable(f"Coinbase returned no candles for {symbol}")

        candles = []
        for row in sorted(raw, key=lambda item: item[0]):
            # Coinbase Exchange candle format: [time, low, high, open, close, volume]
            open_time = datetime.fromtimestamp(int(row[0]), tz=UTC)
            close_time = open_time + timedelta(seconds=granularity)
            if close_time > now:
                continue  # still open: not a closed candle
            is_stale = (now - close_time).total_seconds() > granularity * 3
            candle = {
                "symbol": symbol,
                "exchange": "coinbase_exchange",
                "timeframe": timeframe,
                "granularity_seconds": granularity,
                "open_time": open_time.isoformat(),
                "close_time": close_time.isoformat(),
                "open": float(row[3]),
                "high": float(row[2]),
                "low": float(row[1]),
                "close": float(row[4]),
                "price": float(row[4]),
                "volume": float(row[5]),
                "source": "coinbase_exchange",
                "simulation": False,
                "quality": "stale" if is_stale else "exchange_ohlcv",
                "is_stale": is_stale,
                "ingested_at": now.isoformat(),
            }
            candle["checksum"] = self.candle_checksum(candle)
            candles.append(candle)
        return candles[-periods:]

    async def persist_candles(self, candles: list[dict[str, Any]]) -> None:
        if not self.db or not candles:
            return
        collection = self.db.market_candles
        for candle in candles:
            await collection.update_one(
                {
                    "symbol": candle["symbol"],
                    "exchange": candle["exchange"],
                    "timeframe": candle["timeframe"],
                    "open_time": candle["open_time"],
                },
                {"$set": candle},
                upsert=True,
            )

    async def get_stored_candles(self, symbol: str, timeframe: str = "1m", limit: int = 100) -> list[dict[str, Any]]:
        if not self.db:
            raise MarketDataUnavailable("No database handle configured for stored candle retrieval")
        self.granularity_for(timeframe)
        cursor = (
            self.db.market_candles.find({"symbol": symbol, "timeframe": timeframe}, {"_id": 0})
            .sort("open_time", -1)
            .limit(limit)
        )
        candles = await cursor.to_list(limit)
        return list(reversed(candles))
