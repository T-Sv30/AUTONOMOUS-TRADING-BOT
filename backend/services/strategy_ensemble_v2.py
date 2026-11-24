from typing import Any

from services.strategy_service_v2 import StrategyServiceV2


class StrategyEnsembleV2:
    def __init__(self):
        self.base = StrategyServiceV2()

    def trend_following(self, prices: list[float], has_position: bool) -> dict[str, Any]:
        s = self.base.generate_signal(prices, has_position)
        score = float(s.get("score", 0.0))
        if score > 0:
            score *= 1.15
        return {**s, "strategy": "trend_following", "score": round(score, 4)}

    def mean_reversion(self, prices: list[float], has_position: bool) -> dict[str, Any]:
        s = self.base.generate_signal(prices, has_position)
        z = float(s.get("features", {}).get("zscore_20", 0.0))

        score = 0.0
        reasons = []
        if z <= -1.25:
            score = 2.2
            reasons.append("oversold reversion")
        elif z >= 1.25:
            score = -2.2
            reasons.append("overbought reversion")

        action = "BUY" if score >= 2.0 else "SELL" if score <= -2.0 else "HOLD"
        if has_position and action == "BUY":
            action = "HOLD"
        if not has_position and action == "SELL":
            action = "HOLD"

        return {
            "action": action,
            "confidence": min(95.0, 50.0 + abs(score) * 12.0),
            "score": round(score, 4),
            "reasons": reasons or ["no reversion edge"],
            "features": s.get("features", {}),
            "high_volatility": s.get("high_volatility", False),
            "strategy": "mean_reversion",
        }

    def breakout(self, prices: list[float], has_position: bool) -> dict[str, Any]:
        if len(prices) < 21:
            return {
                "action": "HOLD",
                "confidence": 50.0,
                "score": 0.0,
                "reasons": ["insufficient data"],
                "features": {},
                "high_volatility": False,
                "strategy": "breakout",
            }

        last = prices[-1]
        high20 = max(prices[-21:-1])
        low20 = min(prices[-21:-1])

        score = 0.0
        reasons = []
        if last > high20:
            score = 2.4
            reasons.append("20-period breakout up")
        elif last < low20:
            score = -2.4
            reasons.append("20-period breakout down")

        action = "BUY" if score >= 2.0 else "SELL" if score <= -2.0 else "HOLD"
        if has_position and action == "BUY":
            action = "HOLD"
        if not has_position and action == "SELL":
            action = "HOLD"

        return {
            "action": action,
            "confidence": min(95.0, 50.0 + abs(score) * 12.0),
            "score": round(score, 4),
            "reasons": reasons or ["inside range"],
            "features": {"high20": high20, "low20": low20, "price": last},
            "high_volatility": False,
            "strategy": "breakout",
        }

    def generate_all(self, prices: list[float], has_position: bool) -> list[dict[str, Any]]:
        return [
            self.trend_following(prices, has_position),
            self.mean_reversion(prices, has_position),
            self.breakout(prices, has_position),
        ]
