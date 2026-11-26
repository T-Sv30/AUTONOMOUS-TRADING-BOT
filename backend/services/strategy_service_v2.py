from statistics import mean, pstdev
from typing import Any


class StrategyServiceV2:
    """Transparent multi-factor baseline for paper and shadow evaluation.

    The strategy deliberately avoids opaque prediction. It derives normalized
    trend, momentum, extension, RSI, volatility, and drawdown features from a
    clean price series and emits conservative BUY, SELL, or HOLD decisions.
    """

    MIN_HISTORY = 30

    @staticmethod
    def _safe_div(numerator: float, denominator: float) -> float:
        return numerator / denominator if denominator else 0.0

    @staticmethod
    def _clean_prices(prices: list[float]) -> list[float]:
        clean = []
        for price in prices:
            if price is None:
                continue
            value = float(price)
            if value > 0:
                clean.append(value)
        return clean

    @staticmethod
    def _sma(values: list[float], window: int) -> float | None:
        if window <= 0 or len(values) < window:
            return None
        return mean(values[-window:])

    @staticmethod
    def _ema(values: list[float], window: int) -> float | None:
        if window <= 0 or len(values) < window:
            return None
        alpha = 2.0 / (window + 1.0)
        ema = mean(values[:window])
        for value in values[window:]:
            ema = alpha * value + (1.0 - alpha) * ema
        return ema

    @classmethod
    def _return(cls, values: list[float], periods: int) -> float:
        if len(values) <= periods or values[-periods - 1] == 0:
            return 0.0
        return cls._safe_div(values[-1] - values[-periods - 1], values[-periods - 1])

    @classmethod
    def _returns(cls, values: list[float]) -> list[float]:
        return [cls._safe_div(current - previous, previous) for previous, current in zip(values, values[1:], strict=False)]

    @classmethod
    def _zscore(cls, values: list[float], window: int) -> float:
        if len(values) < window:
            return 0.0
        sample = values[-window:]
        deviation = pstdev(sample)
        return cls._safe_div(values[-1] - mean(sample), deviation)

    @classmethod
    def _rsi(cls, values: list[float], window: int = 14) -> float:
        if len(values) <= window:
            return 50.0
        deltas = [current - previous for previous, current in zip(values[-window - 1 :], values[-window:], strict=False)]
        gains = [delta for delta in deltas if delta > 0]
        losses = [-delta for delta in deltas if delta < 0]
        avg_gain = mean(gains) if gains else 0.0
        avg_loss = mean(losses) if losses else 0.0
        if avg_loss == 0:
            return 100.0 if avg_gain > 0 else 50.0
        relative_strength = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + relative_strength))

    @staticmethod
    def _max_drawdown(values: list[float]) -> float:
        if not values:
            return 0.0
        peak = values[0]
        max_drawdown = 0.0
        for value in values:
            peak = max(peak, value)
            if peak > 0:
                max_drawdown = max(max_drawdown, (peak - value) / peak)
        return max_drawdown

    def features(self, prices: list[float]) -> dict[str, Any]:
        clean = self._clean_prices(prices)
        if not clean:
            return {"price": 0.0, "history": 0, "ready": False, "reason": "no valid prices"}

        returns = self._returns(clean)
        volatility_20 = pstdev(returns[-20:]) if len(returns) >= 20 else 0.0
        volatility_60 = pstdev(returns[-60:]) if len(returns) >= 60 else volatility_20
        ema_8 = self._ema(clean, 8)
        ema_21 = self._ema(clean, 21)
        sma_20 = self._sma(clean, 20)
        sma_50 = self._sma(clean, 50)
        trend_strength = self._safe_div((ema_8 or clean[-1]) - (ema_21 or clean[-1]), ema_21 or clean[-1])

        return {
            "price": round(clean[-1], 8),
            "history": len(clean),
            "ready": len(clean) >= self.MIN_HISTORY,
            "sma_20": round(sma_20, 8) if sma_20 is not None else None,
            "sma_50": round(sma_50, 8) if sma_50 is not None else None,
            "ema_8": round(ema_8, 8) if ema_8 is not None else None,
            "ema_21": round(ema_21, 8) if ema_21 is not None else None,
            "trend_strength": round(trend_strength, 8),
            "return_3": round(self._return(clean, 3), 8),
            "return_5": round(self._return(clean, 5), 8),
            "return_10": round(self._return(clean, 10), 8),
            "return_20": round(self._return(clean, 20), 8),
            "zscore_20": round(self._zscore(clean, 20), 8),
            "rsi_14": round(self._rsi(clean, 14), 8),
            "realized_volatility_20": round(volatility_20, 8),
            "realized_volatility_60": round(volatility_60, 8),
            "volatility_ratio": round(self._safe_div(volatility_20, volatility_60), 8),
            "max_drawdown_20": round(self._max_drawdown(clean[-20:]), 8),
        }

    def score_features(self, feature_set: dict[str, Any]) -> dict[str, Any]:
        if not feature_set.get("ready"):
            return {
                "score": 0.0,
                "confidence": 50.0,
                "reasons": [feature_set.get("reason", "insufficient history")],
                "high_volatility": False,
            }

        score = 0.0
        reasons = []
        trend_strength = float(feature_set.get("trend_strength", 0.0) or 0.0)
        return_5 = float(feature_set.get("return_5", 0.0) or 0.0)
        return_20 = float(feature_set.get("return_20", 0.0) or 0.0)
        zscore_20 = float(feature_set.get("zscore_20", 0.0) or 0.0)
        rsi_14 = float(feature_set.get("rsi_14", 50.0) or 50.0)
        volatility_20 = float(feature_set.get("realized_volatility_20", 0.0) or 0.0)
        volatility_ratio = float(feature_set.get("volatility_ratio", 1.0) or 1.0)
        max_drawdown_20 = float(feature_set.get("max_drawdown_20", 0.0) or 0.0)

        if trend_strength >= 0.003:
            score += 1.25
            reasons.append("positive ema trend")
        elif trend_strength <= -0.003:
            score -= 1.25
            reasons.append("negative ema trend")

        if return_5 >= 0.012:
            score += 0.85
            reasons.append("positive short momentum")
        elif return_5 <= -0.012:
            score -= 0.85
            reasons.append("negative short momentum")

        if return_20 >= 0.03:
            score += 0.65
            reasons.append("positive intermediate momentum")
        elif return_20 <= -0.03:
            score -= 0.65
            reasons.append("negative intermediate momentum")

        if zscore_20 <= -2.0:
            score += 0.45
            reasons.append("deep downside extension")
        elif zscore_20 >= 2.0:
            score -= 0.45
            reasons.append("upside extension risk")

        if rsi_14 <= 35.0:
            score += 0.35
            reasons.append("rsi oversold support")
        elif rsi_14 >= 70.0:
            score -= 0.35
            reasons.append("rsi overbought risk")

        high_volatility = volatility_20 >= 0.035 or volatility_ratio >= 1.8
        if high_volatility:
            score *= 0.65
            reasons.append("high volatility haircut")

        if max_drawdown_20 >= 0.08 and trend_strength <= 0:
            score -= 0.45
            reasons.append("unrecovered drawdown pressure")

        confidence = min(92.0, 50.0 + abs(score) * 13.5)
        if high_volatility:
            confidence = max(45.0, confidence - 10.0)
        if int(feature_set.get("history", 0) or 0) < 50:
            confidence = max(45.0, confidence - 5.0)

        return {
            "score": round(score, 4),
            "confidence": round(confidence, 4),
            "reasons": reasons or ["no material edge"],
            "high_volatility": high_volatility,
        }

    def generate_signal(self, prices: list[float], has_position: bool = False) -> dict[str, Any]:
        feature_set = self.features(prices)
        scored = self.score_features(feature_set)
        score = float(scored["score"])

        if score >= 2.0 and not has_position:
            action = "BUY"
        elif score <= -1.25 and has_position:
            action = "SELL"
        else:
            action = "HOLD"

        return {
            "action": action,
            "confidence": scored["confidence"],
            "score": scored["score"],
            "reasons": scored["reasons"],
            "features": feature_set,
            "high_volatility": scored["high_volatility"],
            "strategy": "multi_factor_baseline",
        }
