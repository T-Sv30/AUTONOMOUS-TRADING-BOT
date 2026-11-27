from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from math import sqrt
from statistics import mean, pstdev
from typing import Any

from services.market_data_service import MarketDataService


def periods_per_year(timeframe: str) -> float:
    """Candles per year for a timeframe, used to annualize Sharpe and Sortino."""
    return 365 * 86400 / MarketDataService.granularity_for(timeframe)


@dataclass
class BacktestConfig:
    initial_cash: float = 10000.0
    fee_bps: float = 10.0
    slippage_bps: float = 5.0
    max_position_pct: float = 0.30
    max_drawdown_pct: float = 0.10
    max_daily_loss_pct: float = 0.05
    fast_window: int = 10
    slow_window: int = 30
    min_trade_notional: float = 10.0
    strategy_name: str = "moving_average_crossover"
    strategy_version: str = "phase2.v1"
    # Candle timeframe of the run; Sharpe and Sortino annualize by it.
    timeframe: str = "1h"


class BacktestingServiceV2:
    """Deterministic backtesting and walk-forward validation engine.

    The engine intentionally avoids exchange calls and live execution. It replays
    supplied candle data through a simple, transparent strategy model, applies
    transaction costs/slippage, records trade events, simulates risk halts, and
    returns benchmark-relative performance metrics.
    """

    @staticmethod
    def utc_now() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _timestamp(candle: dict[str, Any], index: int) -> str:
        return str(candle.get("close_time") or candle.get("timestamp") or candle.get("open_time") or index)

    @staticmethod
    def _date_key(timestamp: str) -> str:
        try:
            return datetime.fromisoformat(timestamp.replace("Z", "+00:00")).date().isoformat()
        except Exception:
            return timestamp[:10]

    @staticmethod
    def _price(candle: dict[str, Any]) -> float:
        return float(candle.get("close", candle.get("price", 0.0)) or 0.0)

    @staticmethod
    def _safe_div(numerator: float, denominator: float) -> float:
        return numerator / denominator if denominator else 0.0

    @staticmethod
    def _pct(value: float) -> float:
        return round(value * 100.0, 6)

    @staticmethod
    def _returns(values: list[float]) -> list[float]:
        out = []
        for previous, current in zip(values, values[1:], strict=False):
            out.append((current - previous) / previous if previous else 0.0)
        return out

    @staticmethod
    def _max_drawdown(values: list[float]) -> float:
        peak = values[0] if values else 0.0
        max_dd = 0.0
        for value in values:
            peak = max(peak, value)
            if peak > 0:
                max_dd = max(max_dd, (peak - value) / peak)
        return max_dd

    @staticmethod
    def _profit_factor(trades: list[dict[str, Any]]) -> float:
        realized = [float(t.get("realized_pnl", 0.0)) for t in trades if t.get("type") == "SELL"]
        gross_profit = sum(pnl for pnl in realized if pnl > 0)
        gross_loss = abs(sum(pnl for pnl in realized if pnl < 0))
        if gross_loss == 0:
            return round(gross_profit, 8) if gross_profit else 0.0
        return round(gross_profit / gross_loss, 8)

    @staticmethod
    def _moving_average(values: list[float], window: int) -> float | None:
        if window <= 0 or len(values) < window:
            return None
        return sum(values[-window:]) / window

    def generate_signal(self, prices: list[float], has_position: bool, config: BacktestConfig) -> dict[str, Any]:
        fast = self._moving_average(prices, config.fast_window)
        slow = self._moving_average(prices, config.slow_window)
        if fast is None or slow is None:
            return {"action": "HOLD", "reason": "insufficient history", "confidence": 50.0}

        spread = self._safe_div(fast - slow, slow)
        confidence = min(95.0, 50.0 + abs(spread) * 2000.0)
        if fast > slow and not has_position:
            action = "BUY"
            reason = "fast_ma_above_slow_ma"
        elif fast < slow and has_position:
            action = "SELL"
            reason = "fast_ma_below_slow_ma"
        else:
            action = "HOLD"
            reason = "no_position_change"
        return {
            "action": action,
            "reason": reason,
            "confidence": round(confidence, 6),
            "fast_ma": round(fast, 8),
            "slow_ma": round(slow, 8),
            "spread_pct": round(spread * 100.0, 6),
            "strategy": config.strategy_name,
            "strategy_version": config.strategy_version,
        }

    def _buy(self, *, cash: float, price: float, timestamp: str, index: int, signal: dict[str, Any], config: BacktestConfig) -> dict[str, Any]:
        notional = cash * config.max_position_pct
        if notional < config.min_trade_notional:
            return {"cash": cash, "units": 0.0, "trade": None}
        fee_rate = config.fee_bps / 10000.0
        slip_rate = config.slippage_bps / 10000.0
        fill_price = price * (1.0 + slip_rate)
        fee = notional * fee_rate
        spendable = max(0.0, notional - fee)
        units = spendable / fill_price if fill_price else 0.0
        return {
            "cash": cash - notional,
            "units": units,
            "trade": {
                "type": "BUY",
                "index": index,
                "timestamp": timestamp,
                "price": round(price, 8),
                "fill_price": round(fill_price, 8),
                "units": round(units, 12),
                "notional": round(notional, 8),
                "fee": round(fee, 8),
                "slippage_bps": config.slippage_bps,
                "signal": signal,
            },
        }

    def _sell(self, *, cash: float, units: float, cost_basis: float, price: float, timestamp: str, index: int, signal: dict[str, Any], config: BacktestConfig) -> dict[str, Any]:
        if units <= 0:
            return {"cash": cash, "units": 0.0, "trade": None}
        fee_rate = config.fee_bps / 10000.0
        slip_rate = config.slippage_bps / 10000.0
        fill_price = price * (1.0 - slip_rate)
        gross = units * fill_price
        fee = gross * fee_rate
        net = gross - fee
        realized_pnl = net - cost_basis
        return {
            "cash": cash + net,
            "units": 0.0,
            "trade": {
                "type": "SELL",
                "index": index,
                "timestamp": timestamp,
                "price": round(price, 8),
                "fill_price": round(fill_price, 8),
                "units": round(units, 12),
                "gross_proceeds": round(gross, 8),
                "fee": round(fee, 8),
                "realized_pnl": round(realized_pnl, 8),
                "slippage_bps": config.slippage_bps,
                "signal": signal,
            },
        }

    def run_moving_average_backtest(self, candles: list[dict[str, Any]], config: BacktestConfig | None = None) -> dict[str, Any]:
        config = config or BacktestConfig()
        normalized = [c for c in candles if self._price(c) > 0]
        if len(normalized) < max(config.slow_window, 2):
            return {
                "status": "insufficient_data",
                "reason": "not enough candles for configured strategy windows",
                "required_periods": max(config.slow_window, 2),
                "received_periods": len(normalized),
                "config": asdict(config),
            }

        cash = float(config.initial_cash)
        units = 0.0
        cost_basis = 0.0
        equity_high = cash
        daily_start_equity: dict[str, float] = {}
        trades: list[dict[str, Any]] = []
        equity_curve: list[dict[str, Any]] = []
        prices: list[float] = []
        halted = False
        halt_reason = ""

        for index, candle in enumerate(normalized):
            price = self._price(candle)
            timestamp = self._timestamp(candle, index)
            day = self._date_key(timestamp)
            prices.append(price)

            equity_before = cash + units * price
            daily_start_equity.setdefault(day, equity_before)
            equity_high = max(equity_high, equity_before)
            drawdown_pct = self._safe_div(equity_high - equity_before, equity_high)
            daily_loss_pct = self._safe_div(daily_start_equity[day] - equity_before, daily_start_equity[day])

            if drawdown_pct >= config.max_drawdown_pct:
                halted = True
                halt_reason = f"max drawdown breached ({drawdown_pct:.2%})"
            if daily_loss_pct >= config.max_daily_loss_pct:
                halted = True
                halt_reason = f"max daily loss breached ({daily_loss_pct:.2%})"

            signal = self.generate_signal(prices, units > 0, config)
            if not halted and signal["action"] == "BUY" and units <= 0:
                result = self._buy(cash=cash, price=price, timestamp=timestamp, index=index, signal=signal, config=config)
                if result["trade"]:
                    cash = result["cash"]
                    units = result["units"]
                    cost_basis = float(result["trade"].get("notional", 0.0))
                    trades.append(result["trade"])
            elif signal["action"] == "SELL" and units > 0:
                result = self._sell(cash=cash, units=units, cost_basis=cost_basis, price=price, timestamp=timestamp, index=index, signal=signal, config=config)
                if result["trade"]:
                    cash = result["cash"]
                    units = result["units"]
                    cost_basis = 0.0
                    trades.append(result["trade"])

            equity = cash + units * price
            equity_high = max(equity_high, equity)
            equity_curve.append(
                {
                    "index": index,
                    "timestamp": timestamp,
                    "price": round(price, 8),
                    "cash": round(cash, 8),
                    "units": round(units, 12),
                    "equity": round(equity, 8),
                    "drawdown_pct": round(self._safe_div(equity_high - equity, equity_high), 8),
                    "halted": halted,
                    "signal": signal["action"],
                }
            )
            if halted:
                break

        final_price = self._price(normalized[min(len(equity_curve), len(normalized)) - 1])
        final_equity = cash + units * final_price
        returns = self._returns([point["equity"] for point in equity_curve])
        sell_trades = [trade for trade in trades if trade.get("type") == "SELL"]
        winning = [trade for trade in sell_trades if float(trade.get("realized_pnl", 0.0)) > 0]
        losing = [trade for trade in sell_trades if float(trade.get("realized_pnl", 0.0)) < 0]
        avg_return = mean(returns) if returns else 0.0
        volatility = pstdev(returns) if len(returns) > 1 else 0.0
        downside = [r for r in returns if r < 0]
        downside_vol = pstdev(downside) if len(downside) > 1 else 0.0
        annual_periods = periods_per_year(config.timeframe)
        sharpe = (avg_return / volatility * sqrt(annual_periods)) if volatility else 0.0
        sortino = (avg_return / downside_vol * sqrt(annual_periods)) if downside_vol else 0.0
        roi = self._safe_div(final_equity - config.initial_cash, config.initial_cash)
        benchmark = self.buy_and_hold_benchmark(normalized, config.initial_cash, timeframe=config.timeframe)

        return {
            "status": "completed" if not halted else "halted",
            "halt_reason": halt_reason,
            "config": asdict(config),
            "summary": {
                "initial_cash": round(config.initial_cash, 8),
                "final_equity": round(final_equity, 8),
                "net_pnl": round(final_equity - config.initial_cash, 8),
                "roi_pct": self._pct(roi),
                "benchmark_roi_pct": benchmark["roi_pct"],
                "alpha_vs_benchmark_pct": round(self._pct(roi) - benchmark["roi_pct"], 6),
                "max_drawdown_pct": self._pct(self._max_drawdown([p["equity"] for p in equity_curve])),
                "sharpe": round(sharpe, 6),
                "sortino": round(sortino, 6),
                "total_trades": len(trades),
                "round_trips": len(sell_trades),
                "win_rate_pct": round(len(winning) / len(sell_trades) * 100.0, 6) if sell_trades else 0.0,
                "profit_factor": self._profit_factor(trades),
                "avg_win": round(mean([float(t["realized_pnl"]) for t in winning]), 8) if winning else 0.0,
                "avg_loss": round(mean([float(t["realized_pnl"]) for t in losing]), 8) if losing else 0.0,
                "exposure_time_pct": round(sum(1 for p in equity_curve if p["units"] > 0) / len(equity_curve) * 100.0, 6) if equity_curve else 0.0,
                "fee_drag": round(sum(float(t.get("fee", 0.0)) for t in trades), 8),
            },
            "benchmark": benchmark,
            "trades": trades,
            "equity_curve": equity_curve,
            "generated_at": self.utc_now(),
        }

    def buy_and_hold_benchmark(self, candles: list[dict[str, Any]], initial_cash: float, timeframe: str = "1h") -> dict[str, Any]:
        prices = [self._price(c) for c in candles if self._price(c) > 0]
        if len(prices) < 2:
            return {"status": "insufficient_data", "roi_pct": 0.0, "final_equity": initial_cash}
        units = initial_cash / prices[0]
        equity_values = [units * price for price in prices]
        final_equity = equity_values[-1]
        roi = self._safe_div(final_equity - initial_cash, initial_cash)
        returns = self._returns(equity_values)
        volatility = pstdev(returns) if len(returns) > 1 else 0.0
        avg_return = mean(returns) if returns else 0.0
        sharpe = (avg_return / volatility * sqrt(periods_per_year(timeframe))) if volatility else 0.0
        return {
            "status": "completed",
            "initial_price": round(prices[0], 8),
            "final_price": round(prices[-1], 8),
            "final_equity": round(final_equity, 8),
            "net_pnl": round(final_equity - initial_cash, 8),
            "roi_pct": self._pct(roi),
            "max_drawdown_pct": self._pct(self._max_drawdown(equity_values)),
            "sharpe": round(sharpe, 6),
        }

    def walk_forward_validation(
        self,
        candles: list[dict[str, Any]],
        train_periods: int = 120,
        test_periods: int = 60,
        config: BacktestConfig | None = None,
    ) -> dict[str, Any]:
        config = config or BacktestConfig()
        normalized = [c for c in candles if self._price(c) > 0]
        if train_periods <= 0 or test_periods <= 0:
            raise ValueError("train_periods and test_periods must be positive")
        if len(normalized) < train_periods + test_periods:
            return {
                "status": "insufficient_data",
                "required_periods": train_periods + test_periods,
                "received_periods": len(normalized),
                "config": asdict(config),
            }

        windows = []
        start = 0
        window_number = 1
        while start + train_periods + test_periods <= len(normalized):
            train = normalized[start : start + train_periods]
            test = normalized[start + train_periods : start + train_periods + test_periods]
            result = self.run_moving_average_backtest(test, config)
            windows.append(
                {
                    "window": window_number,
                    "train_start": self._timestamp(train[0], start),
                    "train_end": self._timestamp(train[-1], start + train_periods - 1),
                    "test_start": self._timestamp(test[0], start + train_periods),
                    "test_end": self._timestamp(test[-1], start + train_periods + test_periods - 1),
                    "summary": result.get("summary", {}),
                    "status": result.get("status"),
                    "halt_reason": result.get("halt_reason", ""),
                }
            )
            start += test_periods
            window_number += 1

        roi_values = [float(w["summary"].get("roi_pct", 0.0)) for w in windows]
        drawdowns = [float(w["summary"].get("max_drawdown_pct", 0.0)) for w in windows]
        return {
            "status": "completed",
            "train_periods": train_periods,
            "test_periods": test_periods,
            "windows": windows,
            "aggregate": {
                "windows": len(windows),
                "avg_roi_pct": round(mean(roi_values), 6) if roi_values else 0.0,
                "median_like_roi_pct": round(sorted(roi_values)[len(roi_values) // 2], 6) if roi_values else 0.0,
                "best_roi_pct": round(max(roi_values), 6) if roi_values else 0.0,
                "worst_roi_pct": round(min(roi_values), 6) if roi_values else 0.0,
                "avg_max_drawdown_pct": round(mean(drawdowns), 6) if drawdowns else 0.0,
                "halted_windows": sum(1 for w in windows if w.get("status") == "halted"),
                "positive_windows": sum(1 for value in roi_values if value > 0),
            },
            "config": asdict(config),
            "generated_at": self.utc_now(),
        }
