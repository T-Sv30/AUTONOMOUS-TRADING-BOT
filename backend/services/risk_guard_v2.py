import logging
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

class RiskGuardV2:
    def __init__(
        self,
        max_position_notional: float = 100.0,
        max_total_exposure_pct: float = 0.30,
        max_open_positions: int = 3,
        max_daily_loss_pct: float = 0.03,
        max_drawdown_pct: float = 0.05,
        cooldown_seconds: int = 300,
    ):
        self.max_position_notional = max_position_notional
        self.max_total_exposure_pct = max_total_exposure_pct
        self.max_open_positions = max_open_positions
        self.max_daily_loss_pct = max_daily_loss_pct
        self.max_drawdown_pct = max_drawdown_pct
        self.cooldown_seconds = cooldown_seconds

    @classmethod
    def from_config(cls, config: dict[str, Any] | None):
        config = config or {}
        return cls(
            max_position_notional=float(config.get("max_position_notional", config.get("max_trade_notional", 100.0))),
            max_total_exposure_pct=float(config.get("max_total_exposure_pct", 0.30)),
            max_open_positions=int(config.get("max_open_positions", 3)),
            max_daily_loss_pct=float(config.get("max_daily_loss", config.get("max_daily_loss_pct", 0.03))),
            max_drawdown_pct=float(config.get("max_drawdown_pct", 0.05)),
            cooldown_seconds=int(config.get("cooldown_seconds", 300)),
        )

    @staticmethod
    def utc_now() -> datetime:
        return datetime.now(UTC)

    def portfolio_metrics(self, state: dict[str, Any], positions: list[dict[str, Any]], price_map: dict[str, float]) -> dict[str, float]:
        cash = float((state or {}).get("cash_balance", 10000.0))
        equity_high = float((state or {}).get("equity_high", max(cash, 10000.0)))
        daily_start_equity = float((state or {}).get("daily_start_equity", max(cash, 10000.0)))

        market_value = 0.0
        for p in positions:
            px = float(price_map.get(p["symbol"], p.get("avg_price", 0.0)))
            market_value += float(p.get("base_units", 0.0)) * px

        total_equity = cash + market_value
        equity_high = max(equity_high, total_equity)
        capital_floor_value = equity_high * (1 - self.max_drawdown_pct)  # display only; drawdown is the one equity halt

        daily_loss_pct = max(0.0, (daily_start_equity - total_equity) / daily_start_equity) if daily_start_equity > 0 else 0.0
        drawdown_pct = max(0.0, (equity_high - total_equity) / equity_high) if equity_high > 0 else 0.0
        exposure_pct = market_value / total_equity if total_equity > 0 else 0.0

        return {
            "cash_balance": round(cash, 8),
            "market_value": round(market_value, 8),
            "total_equity": round(total_equity, 8),
            "equity_high": round(equity_high, 8),
            "daily_start_equity": round(daily_start_equity, 8),
            "capital_floor_value": round(capital_floor_value, 8),
            "daily_loss_pct": round(daily_loss_pct, 8),
            "drawdown_pct": round(drawdown_pct, 8),
            "exposure_pct": round(exposure_pct, 8),
        }

    def should_kill_switch(self, metrics: dict[str, float]) -> dict[str, Any]:
        if metrics["daily_loss_pct"] >= self.max_daily_loss_pct:
            return {"triggered": True, "reason": f"daily loss limit breached ({metrics['daily_loss_pct']:.2%})"}
        if metrics["drawdown_pct"] >= self.max_drawdown_pct:
            return {"triggered": True, "reason": f"drawdown limit breached ({metrics['drawdown_pct']:.2%})"}
        return {"triggered": False, "reason": "ok"}

    def can_open_position(
        self,
        metrics: dict[str, float],
        positions: list[dict[str, Any]],
        proposed_notional: float,
        last_trade_at_iso: str | None = None,
    ) -> dict[str, Any]:
        kill = self.should_kill_switch(metrics)
        if kill["triggered"]:
            return {"allowed": False, "reason": kill["reason"]}
        if proposed_notional <= 0:
            return {"allowed": False, "reason": "invalid proposed notional"}
        notional = min(proposed_notional, self.max_position_notional)
        if len(positions) >= self.max_open_positions:
            return {"allowed": False, "reason": "max open positions reached"}
        if metrics["total_equity"] <= 0:
            return {"allowed": False, "reason": "invalid equity"}

        projected_exposure = metrics["exposure_pct"] + (notional / metrics["total_equity"])
        if projected_exposure > self.max_total_exposure_pct:
            return {"allowed": False, "reason": "total exposure limit exceeded"}

        if last_trade_at_iso:
            try:
                last_trade_at = datetime.fromisoformat(last_trade_at_iso.replace("Z", "+00:00"))
                if (self.utc_now() - last_trade_at).total_seconds() < self.cooldown_seconds:
                    return {"allowed": False, "reason": "trade cooldown active"}
            except Exception as exc:
                logger.warning("Ignoring unparseable last_trade_at %r for cooldown check: %s", last_trade_at_iso, exc)

        return {"allowed": True, "reason": "ok", "notional": notional}
