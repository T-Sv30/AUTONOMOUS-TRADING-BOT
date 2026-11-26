import random

from services.regime_service_v2 import RegimeServiceV2


def test_choppy_series_is_range():
    prices = [100 + (1 if i % 2 else -1) for i in range(60)]
    assert RegimeServiceV2().classify(prices) == "range"


def test_steady_rise_is_trend_up():
    assert RegimeServiceV2().classify([100 + i for i in range(60)]) == "trend_up"


def test_steady_fall_is_trend_down():
    assert RegimeServiceV2().classify([200 - i for i in range(60)]) == "trend_down"


def test_random_walks_produce_range_sometimes():
    rng = random.Random(7)
    seen = set()
    for _ in range(500):
        p = [100.0]
        for _ in range(59):
            p.append(p[-1] * (1 + rng.uniform(-0.01, 0.01)))
        seen.add(RegimeServiceV2().classify(p))
    assert {"range", "trend_up", "trend_down"} <= seen
