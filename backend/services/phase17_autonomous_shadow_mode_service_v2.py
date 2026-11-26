import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from services.strategy_ensemble_v2 import StrategyEnsembleV2


@dataclass(frozen=True)
class ShadowCheck:
    name: str
    passed: bool
    severity: str
    detail: str
    observed: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "name": self.name,
            "passed": self.passed,
            "severity": self.severity,
            "detail": self.detail,
        }
        if self.observed is not None:
            payload["observed"] = self.observed
        return payload


class Phase17AutonomousShadowModeServiceV2:
    """Autonomous live shadow-mode evaluator.

    Shadow mode evaluates autonomous decisions against live-condition inputs but
    never submits orders. It records what the system would have done, the risk
    decision, and simulated outcome evidence for later review.
    """

    # Derived from the strategies the bot runs (Decision 4: the plug-in registry arrives in M4).
    ALLOWED_STRATEGIES = StrategyEnsembleV2.STRATEGY_NAMES
    ALLOWED_SYMBOLS = {"BTC-USD", "ETH-USD"}
    DEFAULT_MIN_CONFIDENCE = 0.70
    DEFAULT_MAX_MARKET_DATA_AGE_SECONDS = 30
    DEFAULT_MAX_SHADOW_NOTIONAL_USD = 5.0
    DEFAULT_REQUIRED_DAYS = 14
    DEFAULT_MIN_DECISIONS = 50
    DEFAULT_MAX_SIMULATED_DRAWDOWN_PCT = 2.0
    DEFAULT_MAX_ERROR_RATE_PCT = 2.0

    def __init__(self, db):
        self.db = db

    @staticmethod
    def utc_now() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def canonical_hash(payload: Mapping[str, Any]) -> str:
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()

    @classmethod
    def policy(cls) -> dict[str, Any]:
        return {
            "phase": "phase_17_autonomous_live_shadow_mode",
            "release_scope": "shadow_mode_only_no_execution",
            "live_order_submission": "not_allowed",
            "autonomous_execution_enabled": False,
            "allowed_strategies": sorted(cls.ALLOWED_STRATEGIES),
            "allowed_symbols": sorted(cls.ALLOWED_SYMBOLS),
            "min_signal_confidence": cls.DEFAULT_MIN_CONFIDENCE,
            "max_market_data_age_seconds": cls.DEFAULT_MAX_MARKET_DATA_AGE_SECONDS,
            "max_shadow_notional_usd": cls.DEFAULT_MAX_SHADOW_NOTIONAL_USD,
            "required_shadow_days": cls.DEFAULT_REQUIRED_DAYS,
            "min_shadow_decisions": cls.DEFAULT_MIN_DECISIONS,
            "max_simulated_drawdown_pct": cls.DEFAULT_MAX_SIMULATED_DRAWDOWN_PCT,
            "max_error_rate_pct": cls.DEFAULT_MAX_ERROR_RATE_PCT,
            "required_outputs": [
                "would_have_traded_decision",
                "risk_decision",
                "simulated_fill_assumption",
                "simulated_pnl_tracking",
                "halt_assumption",
                "reconciliation_assumption",
                "tamper_evident_decision_hash",
            ],
        }

    @staticmethod
    def bool_value(values: Mapping[str, Any], key: str) -> bool:
        value = values.get(key)
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}

    @classmethod
    def validate_input(cls, payload: Mapping[str, Any]) -> dict[str, Any]:
        strategy = str(payload.get("strategy", "")).strip()
        symbol = str(payload.get("symbol", "")).upper()
        side = str(payload.get("side", "HOLD")).upper()
        try:
            confidence = float(payload.get("confidence", 0) or 0)
        except (TypeError, ValueError):
            confidence = 0.0
        try:
            market_data_age_seconds = int(payload.get("market_data_age_seconds", 999999) or 999999)
        except (TypeError, ValueError):
            market_data_age_seconds = 999999
        try:
            notional_usd = float(payload.get("notional_usd", 0) or 0)
        except (TypeError, ValueError):
            notional_usd = 0.0

        checks: list[ShadowCheck] = [
            ShadowCheck(
                name="shadow_mode_explicit",
                passed=payload.get("mode") == "shadow",
                severity="critical",
                detail="Autonomous decisions must be evaluated only in explicit shadow mode.",
                observed={"mode": payload.get("mode")},
            ),
            ShadowCheck(
                name="live_order_submission_disabled",
                passed=not cls.bool_value(payload, "submit_live_order") and not cls.bool_value(payload, "autonomous_execution_enabled"),
                severity="critical",
                detail="Shadow mode must not submit live orders or enable autonomous execution.",
                observed={
                    "submit_live_order": payload.get("submit_live_order"),
                    "autonomous_execution_enabled": payload.get("autonomous_execution_enabled"),
                },
            ),
            ShadowCheck(
                name="strategy_allowlisted",
                passed=strategy in cls.ALLOWED_STRATEGIES,
                severity="critical",
                detail="Shadow-mode strategy must be allowlisted by Phase 16 policy.",
                observed={"strategy": strategy},
            ),
            ShadowCheck(
                name="symbol_allowlisted",
                passed=symbol in cls.ALLOWED_SYMBOLS,
                severity="critical",
                detail="Shadow-mode symbol must remain in the approved spot allowlist.",
                observed={"symbol": symbol},
            ),
            ShadowCheck(
                name="side_is_supported",
                passed=side in {"BUY", "SELL", "HOLD"},
                severity="critical",
                detail="Shadow-mode side must be BUY, SELL, or HOLD.",
                observed={"side": side},
            ),
            ShadowCheck(
                name="confidence_threshold_met_or_hold",
                passed=side == "HOLD" or confidence >= cls.DEFAULT_MIN_CONFIDENCE,
                severity="critical",
                detail="Would-trade decisions require the minimum signal confidence; HOLD is allowed below threshold.",
                observed={"side": side, "confidence": confidence, "min_confidence": cls.DEFAULT_MIN_CONFIDENCE},
            ),
            ShadowCheck(
                name="market_data_fresh_enough",
                passed=0 <= market_data_age_seconds <= cls.DEFAULT_MAX_MARKET_DATA_AGE_SECONDS,
                severity="critical",
                detail="Shadow-mode decision inputs must use fresh live-condition market data.",
                observed={"market_data_age_seconds": market_data_age_seconds},
            ),
            ShadowCheck(
                name="shadow_notional_limited_or_hold",
                passed=side == "HOLD" or (0.0 < notional_usd <= cls.DEFAULT_MAX_SHADOW_NOTIONAL_USD),
                severity="critical",
                detail="Would-trade notional must remain within the shadow-mode cap.",
                observed={"side": side, "notional_usd": notional_usd, "cap": cls.DEFAULT_MAX_SHADOW_NOTIONAL_USD},
            ),
            ShadowCheck(
                name="risk_decision_present",
                passed=isinstance(payload.get("risk_decision"), dict) and payload.get("risk_decision", {}).get("decision") in {"allow", "deny", "hold"},
                severity="critical",
                detail="Every shadow-mode decision must include a risk decision artifact.",
                observed={"risk_decision": payload.get("risk_decision")},
            ),
            ShadowCheck(
                name="halt_and_reconciliation_assumptions_present",
                passed=isinstance(payload.get("halt_assumption"), dict) and isinstance(payload.get("reconciliation_assumption"), dict),
                severity="critical",
                detail="Shadow-mode evidence must include halt and reconciliation assumptions.",
                observed={
                    "halt_assumption_present": isinstance(payload.get("halt_assumption"), dict),
                    "reconciliation_assumption_present": isinstance(payload.get("reconciliation_assumption"), dict),
                },
            ),
        ]
        serialized = [check.to_dict() for check in checks]
        blockers = [check for check in serialized if check["severity"] == "critical" and not check["passed"]]
        return {
            "status": "valid" if not blockers else "blocked",
            "valid_shadow_decision_input": not blockers,
            "blockers": blockers,
            "checks": serialized,
        }

    @classmethod
    def build_shadow_decision(cls, payload: Mapping[str, Any]) -> dict[str, Any]:
        validation = cls.validate_input(payload)
        strategy = str(payload.get("strategy", "")).strip()
        symbol = str(payload.get("symbol", "")).upper()
        side = str(payload.get("side", "HOLD")).upper()
        confidence = float(payload.get("confidence", 0) or 0)
        notional_usd = float(payload.get("notional_usd", 0) or 0)
        reference_price = float(payload.get("reference_price", 0) or 0)
        quantity = notional_usd / reference_price if side in {"BUY", "SELL"} and reference_price > 0 else 0.0
        would_trade = validation["valid_shadow_decision_input"] and side in {"BUY", "SELL"} and payload.get("risk_decision", {}).get("decision") == "allow"
        decision = {
            "mode": "shadow",
            "would_submit_live_order": False,
            "would_trade": would_trade,
            "strategy": strategy,
            "symbol": symbol,
            "side": side,
            "confidence": confidence,
            "notional_usd": notional_usd if would_trade else 0.0,
            "reference_price": reference_price,
            "simulated_quantity": quantity if would_trade else 0.0,
            "risk_decision": payload.get("risk_decision", {}),
            "simulated_fill_assumption": {
                "type": "reference_price_fill",
                "price": reference_price,
                "slippage_bps": float(payload.get("slippage_bps", 0) or 0),
            },
            "halt_assumption": payload.get("halt_assumption", {}),
            "reconciliation_assumption": payload.get("reconciliation_assumption", {}),
            "validation": validation,
            "created_at": cls.utc_now(),
        }
        decision["decision_hash"] = cls.canonical_hash(decision)
        return decision

    @classmethod
    def evaluate_shadow_window(cls, summary: Mapping[str, Any]) -> dict[str, Any]:
        try:
            days = int(summary.get("shadow_days", 0) or 0)
            decisions = int(summary.get("decision_count", 0) or 0)
            would_trades = int(summary.get("would_trade_count", 0) or 0)
            error_count = int(summary.get("error_count", 0) or 0)
            max_drawdown_pct = float(summary.get("max_simulated_drawdown_pct", 0) or 0)
            simulated_pnl_usd = float(summary.get("simulated_pnl_usd", 0) or 0)
            halt_count = int(summary.get("halt_count", 0) or 0)
            reconciliation_issue_count = int(summary.get("reconciliation_issue_count", 0) or 0)
        except (TypeError, ValueError):
            days = decisions = would_trades = error_count = halt_count = reconciliation_issue_count = 0
            max_drawdown_pct = simulated_pnl_usd = 0.0
        error_rate = (error_count / decisions * 100.0) if decisions > 0 else 100.0

        checks = [
            ShadowCheck(
                name="minimum_shadow_duration_met",
                passed=days >= cls.DEFAULT_REQUIRED_DAYS,
                severity="critical",
                detail="Shadow mode must run long enough to observe live-condition behavior.",
                observed={"shadow_days": days, "required": cls.DEFAULT_REQUIRED_DAYS},
            ),
            ShadowCheck(
                name="minimum_decision_count_met",
                passed=decisions >= cls.DEFAULT_MIN_DECISIONS,
                severity="critical",
                detail="Shadow mode must produce enough decisions for review.",
                observed={"decision_count": decisions, "required": cls.DEFAULT_MIN_DECISIONS},
            ),
            ShadowCheck(
                name="would_trade_decisions_observed",
                passed=would_trades > 0,
                severity="critical",
                detail="Shadow mode must observe at least one would-trade decision before canary review.",
                observed={"would_trade_count": would_trades},
            ),
            ShadowCheck(
                name="simulated_drawdown_within_limit",
                passed=max_drawdown_pct <= cls.DEFAULT_MAX_SIMULATED_DRAWDOWN_PCT,
                severity="critical",
                detail="Simulated shadow drawdown must remain within the design limit.",
                observed={"max_simulated_drawdown_pct": max_drawdown_pct, "limit": cls.DEFAULT_MAX_SIMULATED_DRAWDOWN_PCT},
            ),
            ShadowCheck(
                name="error_rate_within_limit",
                passed=error_rate <= cls.DEFAULT_MAX_ERROR_RATE_PCT,
                severity="critical",
                detail="Shadow-mode operational error rate must remain low.",
                observed={"error_count": error_count, "decision_count": decisions, "error_rate_pct": error_rate},
            ),
            ShadowCheck(
                name="no_halts_or_reconciliation_issues",
                passed=halt_count == 0 and reconciliation_issue_count == 0,
                severity="critical",
                detail="Shadow mode must not reveal halt or reconciliation issues before canary consideration.",
                observed={"halt_count": halt_count, "reconciliation_issue_count": reconciliation_issue_count},
            ),
            ShadowCheck(
                name="simulated_pnl_recorded",
                passed="simulated_pnl_usd" in summary,
                severity="warning",
                detail="Simulated P/L should be recorded for review even if not used as the only acceptance criterion.",
                observed={"simulated_pnl_usd": simulated_pnl_usd},
            ),
        ]
        serialized = [check.to_dict() for check in checks]
        blockers = [check for check in serialized if check["severity"] == "critical" and not check["passed"]]
        warnings = [check for check in serialized if check["severity"] == "warning" and not check["passed"]]
        return {
            "status": "shadow_review_passed" if not blockers else "blocked",
            "ready_for_autonomous_canary_review": not blockers,
            "autonomous_execution_enabled": False,
            "blockers": blockers,
            "warnings": warnings,
            "checks": serialized,
            "policy": cls.policy(),
            "evaluated_at": cls.utc_now(),
        }

    async def record_shadow_decision(self, user_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        decision = self.build_shadow_decision(payload)
        await self.db.autonomous_shadow_decisions.insert_one({"user_id": user_id, **decision})
        return decision

    async def record_shadow_window_review(self, user_id: str, summary: Mapping[str, Any]) -> dict[str, Any]:
        result = self.evaluate_shadow_window(summary)
        await self.db.autonomous_shadow_window_reviews.insert_one({
            "user_id": user_id,
            "summary": dict(summary),
            "result": result,
            "created_at": self.utc_now(),
        })
        return result
