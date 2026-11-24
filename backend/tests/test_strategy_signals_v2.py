from services.strategy_ensemble_v2 import StrategyEnsembleV2
from services.strategy_service_v2 import StrategyServiceV2


def geometric(count: int, start: float = 100.0, step: float = 0.01) -> list[float]:
    prices = [start]
    for _ in range(count - 1):
        prices.append(prices[-1] * (1 + step))
    return prices


def test_features_require_minimum_clean_history():
    service = StrategyServiceV2()

    assert service.features([])["ready"] is False
    assert service.features([None, 0, -5])["reason"] == "no valid prices"

    short = service.features(geometric(StrategyServiceV2.MIN_HISTORY - 1))
    assert short["ready"] is False
    assert service.score_features(short)["score"] == 0.0

    ready = service.features([None, *geometric(StrategyServiceV2.MIN_HISTORY)])
    assert ready["ready"] is True
    assert ready["history"] == StrategyServiceV2.MIN_HISTORY


def test_steady_uptrend_buys_when_flat_and_holds_when_long():
    service = StrategyServiceV2()
    prices = geometric(60, step=0.01)

    flat = service.generate_signal(prices, has_position=False)
    assert flat["action"] == "BUY"
    assert flat["score"] >= 2.0
    assert "positive ema trend" in flat["reasons"]
    assert flat["features"]["rsi_14"] == 100.0

    long = service.generate_signal(prices, has_position=True)
    assert long["action"] == "HOLD"


def test_steady_downtrend_sells_an_open_position():
    service = StrategyServiceV2()
    prices = geometric(60, step=-0.01)

    signal = service.generate_signal(prices, has_position=True)

    assert signal["action"] == "SELL"
    assert signal["score"] <= -1.25
    assert "negative ema trend" in signal["reasons"]
    assert "unrecovered drawdown pressure" in signal["reasons"]
    assert service.generate_signal(prices, has_position=False)["action"] == "HOLD"


def test_flat_market_has_no_edge():
    signal = StrategyServiceV2().generate_signal([100.0] * 60)

    assert signal["action"] == "HOLD"
    assert signal["reasons"] == ["no material edge"]
    assert signal["features"]["rsi_14"] == 50.0


def test_choppy_market_applies_volatility_haircut():
    prices = [100.0 * (1.06 if i % 2 else 0.94) for i in range(60)]

    scored = StrategyServiceV2().score_features(StrategyServiceV2().features(prices))

    assert scored["high_volatility"] is True
    assert "high volatility haircut" in scored["reasons"]
    assert scored["confidence"] >= 45.0


def test_breakout_respects_position_and_history():
    ensemble = StrategyEnsembleV2()
    base = [100.0 + (i % 3) * 0.1 for i in range(40)]

    assert ensemble.breakout(base[:20], has_position=False)["reasons"] == ["insufficient data"]

    up = ensemble.breakout([*base, 105.0], has_position=False)
    assert up["action"] == "BUY"
    assert up["reasons"] == ["20-period breakout up"]
    assert ensemble.breakout([*base, 105.0], has_position=True)["action"] == "HOLD"

    down = ensemble.breakout([*base, 95.0], has_position=True)
    assert down["action"] == "SELL"
    assert ensemble.breakout([*base, 95.0], has_position=False)["action"] == "HOLD"

    inside = ensemble.breakout([*base, 100.1], has_position=False)
    assert inside["action"] == "HOLD"
    assert inside["reasons"] == ["inside range"]


def test_mean_reversion_buys_sharp_drop_and_sells_sharp_spike():
    ensemble = StrategyEnsembleV2()
    base = [100.0 + (i % 2) * 0.5 for i in range(40)]

    dip = ensemble.mean_reversion([*base, 90.0], has_position=False)
    assert dip["action"] == "BUY"
    assert dip["reasons"] == ["oversold reversion"]

    spike = ensemble.mean_reversion([*base, 110.0], has_position=True)
    assert spike["action"] == "SELL"
    assert ensemble.mean_reversion([*base, 110.0], has_position=False)["action"] == "HOLD"


def test_ensemble_runs_all_three_strategies():
    signals = StrategyEnsembleV2().generate_all(geometric(60), has_position=False)

    assert [s["strategy"] for s in signals] == ["trend_following", "mean_reversion", "breakout"]
    trend = signals[0]
    assert trend["score"] > StrategyServiceV2().generate_signal(geometric(60))["score"]
