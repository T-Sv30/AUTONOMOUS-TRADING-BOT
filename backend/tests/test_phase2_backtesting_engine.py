from datetime import UTC, datetime, timedelta
from math import sqrt
from statistics import mean, pstdev

import pytest

from services.backtesting_service_v2 import BacktestConfig, BacktestingServiceV2


def make_candles(count=120, start_price=100.0, drift=0.01):
    start = datetime(2026, 1, 1, tzinfo=UTC)
    candles = []
    price = start_price
    for i in range(count):
        open_price = price
        close_price = open_price * (1 + drift)
        high = max(open_price, close_price) * 1.001
        low = min(open_price, close_price) * 0.999
        open_time = start + timedelta(hours=i)
        close_time = open_time + timedelta(hours=1)
        candles.append(
            {
                "symbol": "BTC-USD",
                "timeframe": "1h",
                "open_time": open_time.isoformat(),
                "close_time": close_time.isoformat(),
                "open": round(open_price, 8),
                "high": round(high, 8),
                "low": round(low, 8),
                "close": round(close_price, 8),
                "volume": 100 + i,
            }
        )
        price = close_price
    return candles


def make_reversal_candles():
    up = make_candles(count=80, start_price=100.0, drift=0.01)
    start_price = up[-1]["close"]
    down = make_candles(count=80, start_price=start_price, drift=-0.01)
    offset = datetime.fromisoformat(up[-1]["close_time"]) + timedelta(hours=1)
    for i, candle in enumerate(down):
        open_time = offset + timedelta(hours=i)
        candle["open_time"] = open_time.isoformat()
        candle["close_time"] = (open_time + timedelta(hours=1)).isoformat()
    return up + down


def test_backtest_reports_insufficient_data():
    service = BacktestingServiceV2()
    result = service.run_moving_average_backtest(make_candles(count=5), BacktestConfig(slow_window=30))
    assert result["status"] == "insufficient_data"
    assert result["required_periods"] == 30


def test_backtest_runs_with_costs_benchmark_and_metrics():
    service = BacktestingServiceV2()
    config = BacktestConfig(fast_window=3, slow_window=8, fee_bps=10, slippage_bps=5, max_position_pct=0.5)
    result = service.run_moving_average_backtest(make_candles(count=80, drift=0.01), config)

    assert result["status"] == "completed"
    assert result["summary"]["total_trades"] >= 1
    assert "benchmark_roi_pct" in result["summary"]
    assert "alpha_vs_benchmark_pct" in result["summary"]
    assert "fee_drag" in result["summary"]
    assert result["benchmark"]["status"] == "completed"
    assert result["equity_curve"]


def test_backtest_executes_round_trip_on_reversal():
    service = BacktestingServiceV2()
    config = BacktestConfig(fast_window=3, slow_window=8, fee_bps=0, slippage_bps=0, max_position_pct=0.5)
    result = service.run_moving_average_backtest(make_reversal_candles(), config)

    sides = [trade["type"] for trade in result["trades"]]
    assert "BUY" in sides
    assert "SELL" in sides
    assert result["summary"]["round_trips"] >= 1
    assert "profit_factor" in result["summary"]


def test_backtest_risk_halt_triggers_on_drawdown():
    service = BacktestingServiceV2()
    config = BacktestConfig(
        fast_window=2,
        slow_window=3,
        fee_bps=0,
        slippage_bps=0,
        max_position_pct=1.0,
        # Two -1% bars before the crossover exit give a 1.99% drawdown (1 - 0.99**2),
        # so the threshold must sit below that for the halt to trigger.
        max_drawdown_pct=0.015,
    )
    result = service.run_moving_average_backtest(make_reversal_candles(), config)

    assert result["status"] == "halted"
    assert "drawdown" in result["halt_reason"]
    assert result["equity_curve"][-1]["halted"] is True


def test_walk_forward_validation_generates_windows_and_aggregate_metrics():
    service = BacktestingServiceV2()
    config = BacktestConfig(fast_window=3, slow_window=8)
    result = service.walk_forward_validation(
        make_candles(count=240, drift=0.002),
        train_periods=60,
        test_periods=45,
        config=config,
    )

    assert result["status"] == "completed"
    assert result["windows"]
    assert result["aggregate"]["windows"] == len(result["windows"])
    assert "avg_roi_pct" in result["aggregate"]


def test_walk_forward_validation_rejects_bad_window_sizes():
    service = BacktestingServiceV2()
    with pytest.raises(ValueError):
        service.walk_forward_validation(make_candles(count=120), train_periods=0, test_periods=30)


def expected_sharpe(values, periods_per_year):
    returns = BacktestingServiceV2._returns(values)
    return mean(returns) / pstdev(returns) * sqrt(periods_per_year)


@pytest.mark.parametrize("timeframe,periods_per_year", [("1h", 8760.0), ("1d", 365.0), ("1m", 525600.0)])
def test_backtest_sharpe_annualizes_by_the_run_timeframe(timeframe, periods_per_year):
    from api_routes_v3 import BacktestRequest

    config = BacktestRequest(timeframe=timeframe, fast_window=3, slow_window=8, fee_bps=0, slippage_bps=0, max_position_pct=0.5).to_config()
    candles = make_reversal_candles()
    result = BacktestingServiceV2().run_moving_average_backtest(candles, config)

    equities = [point["equity"] for point in result["equity_curve"]]
    strategy_returns = BacktestingServiceV2._returns(equities)
    assert pstdev(strategy_returns) > 0
    assert abs(result["summary"]["sharpe"] - round(expected_sharpe(equities, periods_per_year), 6)) < 1e-6

    prices = [candle["close"] for candle in candles]
    assert abs(result["benchmark"]["sharpe"] - round(expected_sharpe(prices, periods_per_year), 6)) < 1e-6


def test_periods_per_year_follows_market_data_granularity():
    from services.backtesting_service_v2 import periods_per_year

    assert periods_per_year("1h") == pytest.approx(8760.0)
    assert periods_per_year("1d") == pytest.approx(365.0)
    assert periods_per_year("5m") == pytest.approx(105120.0)
    with pytest.raises(ValueError):
        periods_per_year("2m")
