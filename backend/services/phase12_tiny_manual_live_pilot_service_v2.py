from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class PilotControlCheck:
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


class Phase12TinyManualLivePilotServiceV2:
    """Control layer for the first tiny human-approved manual live pilot.

    This service does not place live orders. It determines whether an operator
    may proceed to the existing manually gated live order endpoint for exactly
    one tiny pilot attempt after all Phase 5-11 prerequisites are satisfied.
    """

    DEFAULT_SYMBOLS = {"BTC-USD", "ETH-USD"}
    DEFAULT_MAX_NOTIONAL_USD = 5.0
    REQUIRED_ACKNOWLEDGEMENT = "I understand this is a one-order tiny manual live pilot and I will reconcile immediately."

    def __init__(self, db):
        self.db = db

    @staticmethod
    def utc_now() -> str:
        return datetime.now(UTC).isoformat()

    @classmethod
    def plan(cls) -> dict[str, Any]:
        return {
            "phase": "phase_12_tiny_manual_live_pilot",
            "live_order_submission": "human_triggered_only_existing_manual_endpoint",
            "max_orders": 1,
            "preferred_notional_usd": {"min": 1.0, "max": cls.DEFAULT_MAX_NOTIONAL_USD},
            "allowed_symbols": sorted(cls.DEFAULT_SYMBOLS),
            "required_acknowledgement": cls.REQUIRED_ACKNOWLEDGEMENT,
            "mandatory_post_submit_steps": [
                "restore_kill_switch",
                "fetch_live_readonly_orders",
                "fetch_live_readonly_fills",
                "run_live_readonly_reconciliation",
                "resolve_post_submit_reconciliation_requirement",
                "build_pilot_report",
                "operator_signoff",
                "stop_after_one_order",
            ],
        }

    async def active_or_unreviewed_pilots(self, user_id: str) -> dict[str, Any]:
        pending_requirements = await self.db.live_post_submit_reconciliation_requirements.find(
            {"user_id": user_id, "status": "pending"}, {"_id": 0}
        ).sort("created_at", -1).limit(20).to_list(20)
        complete_reports = await self.db.manual_live_pilot_reports.find(
            {"user_id": user_id, "status": "complete"}, {"_id": 0}
        ).sort("generated_at", -1).limit(20).to_list(20)
        signoffs = await self.db.manual_live_pilot_signoffs.find({"user_id": user_id}, {"_id": 0}).limit(100).to_list(100)
        signed_order_ids = {item.get("live_order_id") for item in signoffs}
        unsigned_reports = [report for report in complete_reports if report.get("live_order_id") not in signed_order_ids]
        return {
            "pending_reconciliation_count": len(pending_requirements),
            "pending_reconciliation": pending_requirements,
            "unsigned_completed_report_count": len(unsigned_reports),
            "unsigned_completed_reports": unsigned_reports,
        }

    @classmethod
    def validate_candidate(
        cls,
        *,
        symbol: str,
        side: str,
        notional_usd: float,
        operator_acknowledgement: str,
        readiness: dict[str, Any],
        expansion_status: dict[str, Any],
        latest_dry_run: dict[str, Any],
        active_state: dict[str, Any],
        max_notional_usd: float = DEFAULT_MAX_NOTIONAL_USD,
    ) -> dict[str, Any]:
        symbol = str(symbol or "").upper()
        side = str(side or "").upper()
        try:
            notional = float(notional_usd)
        except (TypeError, ValueError):
            notional = 0.0

        dry_run_order = latest_dry_run.get("order") if isinstance(latest_dry_run.get("order"), dict) else {}
        dry_run_requested = dry_run_order.get("requested") if isinstance(dry_run_order.get("requested"), dict) else latest_dry_run.get("requested", {})
        checks: list[PilotControlCheck] = [
            PilotControlCheck(
                name="operator_acknowledgement_exact",
                passed=operator_acknowledgement == cls.REQUIRED_ACKNOWLEDGEMENT,
                severity="critical",
                detail="Operator must provide the exact one-order pilot acknowledgement.",
                observed={"provided": bool(operator_acknowledgement)},
            ),
            PilotControlCheck(
                name="symbol_allowlisted_for_first_pilot",
                passed=symbol in cls.DEFAULT_SYMBOLS,
                severity="critical",
                detail="First tiny pilot is restricted to BTC-USD or ETH-USD.",
                observed={"symbol": symbol, "allowed_symbols": sorted(cls.DEFAULT_SYMBOLS)},
            ),
            PilotControlCheck(
                name="side_is_market_buy_or_sell",
                passed=side in {"BUY", "SELL"},
                severity="critical",
                detail="Pilot side must be BUY or SELL through the existing manual market endpoint.",
                observed={"side": side},
            ),
            PilotControlCheck(
                name="notional_is_positive_and_tiny",
                passed=0.0 < notional <= float(max_notional_usd) <= cls.DEFAULT_MAX_NOTIONAL_USD,
                severity="critical",
                detail="Pilot notional must be positive and no larger than the tiny pilot cap.",
                observed={"notional_usd": notional, "max_notional_usd": max_notional_usd},
            ),
            PilotControlCheck(
                name="pilot_readiness_clear",
                passed=bool(readiness.get("ready")) and readiness.get("status") == "ready" and not readiness.get("blockers"),
                severity="critical",
                detail="Phase 5 pilot readiness must be clear immediately before the live pilot.",
                observed={"ready": readiness.get("ready"), "status": readiness.get("status"), "blockers": readiness.get("blockers", [])},
            ),
            PilotControlCheck(
                name="expansion_status_clear",
                passed=bool(expansion_status.get("allowed_to_repeat_pilot")) and not expansion_status.get("blockers"),
                severity="critical",
                detail="Phase 7 expansion status must be clear before a pilot attempt.",
                observed={"allowed_to_repeat_pilot": expansion_status.get("allowed_to_repeat_pilot"), "blockers": expansion_status.get("blockers", [])},
            ),
            PilotControlCheck(
                name="no_pending_reconciliation_or_unsigned_reports",
                passed=active_state.get("pending_reconciliation_count", 0) == 0 and active_state.get("unsigned_completed_report_count", 0) == 0,
                severity="critical",
                detail="No pending reconciliation or unsigned completed pilot report may exist before another pilot.",
                observed={
                    "pending_reconciliation_count": active_state.get("pending_reconciliation_count", 0),
                    "unsigned_completed_report_count": active_state.get("unsigned_completed_report_count", 0),
                },
            ),
            PilotControlCheck(
                name="latest_dry_run_successful",
                passed=latest_dry_run.get("status") == "dry_run" or dry_run_order.get("status") == "dry_run",
                severity="critical",
                detail="The exact intended order must have been rehearsed with dry_run=true immediately before live pilot consideration.",
                observed={"response_status": latest_dry_run.get("status"), "order_status": dry_run_order.get("status")},
            ),
            PilotControlCheck(
                name="latest_dry_run_not_live_execution",
                passed=dry_run_order.get("live_execution") is not True,
                severity="critical",
                detail="The prerequisite dry-run artifact must not indicate real live execution.",
                observed={"live_execution": dry_run_order.get("live_execution")},
            ),
            PilotControlCheck(
                name="latest_dry_run_matches_candidate_order",
                passed=(
                    str(dry_run_requested.get("symbol", "")).upper() == symbol
                    and str(dry_run_requested.get("side", "")).upper() == side
                    and abs(float(dry_run_requested.get("notional_usd", notional) or notional) - notional) < 0.000001
                ),
                severity="critical",
                detail="Dry-run artifact must match the exact symbol, side, and notional of the intended pilot.",
                observed={"requested": dry_run_requested, "candidate": {"symbol": symbol, "side": side, "notional_usd": notional}},
            ),
        ]
        serialized = [check.to_dict() for check in checks]
        blockers = [check for check in serialized if check["severity"] == "critical" and not check["passed"]]
        return {
            "status": "eligible" if not blockers else "blocked",
            "eligible_for_one_tiny_manual_live_pilot": not blockers,
            "blockers": blockers,
            "checks": serialized,
            "plan": cls.plan(),
        }

    async def record_candidate_decision(
        self,
        *,
        user_id: str,
        symbol: str,
        side: str,
        notional_usd: float,
        operator_acknowledgement: str,
        readiness: dict[str, Any],
        expansion_status: dict[str, Any],
        latest_dry_run: dict[str, Any],
        max_notional_usd: float = DEFAULT_MAX_NOTIONAL_USD,
    ) -> dict[str, Any]:
        active_state = await self.active_or_unreviewed_pilots(user_id)
        decision = self.validate_candidate(
            symbol=symbol,
            side=side,
            notional_usd=notional_usd,
            operator_acknowledgement=operator_acknowledgement,
            readiness=readiness,
            expansion_status=expansion_status,
            latest_dry_run=latest_dry_run,
            active_state=active_state,
            max_notional_usd=max_notional_usd,
        )
        record = {
            "user_id": user_id,
            "symbol": str(symbol).upper(),
            "side": str(side).upper(),
            "notional_usd": float(notional_usd),
            "decision": decision,
            "created_at": self.utc_now(),
        }
        await self.db.manual_live_pilot_candidate_decisions.insert_one(record)
        return record
