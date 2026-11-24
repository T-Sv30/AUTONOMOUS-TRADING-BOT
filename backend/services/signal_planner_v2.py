from typing import Any

from services.allocator_v2 import AllocatorV2
from services.execution_optimizer_v2 import ExecutionOptimizerV2
from services.regime_service_v2 import RegimeServiceV2
from services.strategy_ensemble_v2 import StrategyEnsembleV2


class SignalPlannerV2:
    def __init__(self):
        self.exec_opt = ExecutionOptimizerV2()
        self.ensemble = StrategyEnsembleV2()
        self.allocator = AllocatorV2()
        self.regime = RegimeServiceV2()

    def build_plan(
        self,
        symbol: str,
        prices: list[float],
        has_position: bool,
        base_notional: float = 100.0,
    ) -> dict[str, Any]:
        if len(prices) < 30:
            return {"symbol": symbol, "action": "HOLD", "reason": "insufficient price history"}

        regime = self.regime.classify(prices)
        signals = self.ensemble.generate_all(prices, has_position=has_position)

        if regime == "range":
            signals = [signal for signal in signals if signal["strategy"] != "trend_following"]
        elif regime == "trend_up":
            signals = [signal for signal in signals if signal["strategy"] != "mean_reversion"]

        allocation = self.allocator.allocate(signals, base_notional=base_notional)
        volatility = abs((prices[-1] - prices[-10]) / prices[-10]) if prices[-10] else 0.0
        final_notional = self.exec_opt.shape_notional(
            allocation.get("notional", 0.0),
            allocation.get("selected", {}).get("confidence", 50.0),
            volatility,
        )

        return {
            "symbol": symbol,
            "action": allocation.get("action", "HOLD"),
            "regime": regime,
            "signals": signals,
            "allocation": allocation,
            "selected": allocation.get("selected", {}),
            "notional": final_notional,
            "volatility": volatility,
        }
