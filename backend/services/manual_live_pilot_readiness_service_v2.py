from datetime import UTC, datetime
from typing import Any

from services.coinbase_live_execution_adapter_v2 import CoinbaseLiveExecutionAdapterV2
from services.live_readonly_service_v2 import LiveReadonlyServiceV2
from services.live_trading_gate_v2 import LiveTradingGateV2
from services.trading_mode_v2 import TradingModeService


class ManualLivePilotReadinessServiceV2:
    """Readiness gate for a tiny human-approved manual live pilot.

    This service does not place orders. It only reports whether the current
    account state is safe enough to consider a tiny manually approved live pilot.
    """

    DEFAULT_RECONCILIATION_MAX_AGE_SECONDS = 300

    def __init__(self, db):
        self.db = db
        self.live_readonly = LiveReadonlyServiceV2(db)
        self.gate = LiveTradingGateV2()

    @staticmethod
    def utc_now() -> datetime:
        return datetime.now(UTC)

    @staticmethod
    def iso(value: datetime) -> str:
        return value.astimezone(UTC).isoformat()

    @staticmethod
    def parse_datetime(value: Any) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed.astimezone(UTC)
        except (TypeError, ValueError):
            return None

    @classmethod
    def is_fresh(cls, value: Any, max_age_seconds: int) -> tuple[bool, float | None]:
        parsed = cls.parse_datetime(value)
        if not parsed:
            return False, None
        age = (cls.utc_now() - parsed).total_seconds()
        return age <= max_age_seconds, age

    async def latest_reconciliation_status(self, user_id: str, max_age_seconds: int = DEFAULT_RECONCILIATION_MAX_AGE_SECONDS) -> dict[str, Any]:
        latest = await self.db.live_readonly_reports.find_one({"user_id": user_id}, {"_id": 0}, sort=[("checked_at", -1)])
        if not latest:
            return {"status": "missing", "fresh": False, "reason": "no live-readonly reconciliation report found", "max_age_seconds": max_age_seconds}
        fresh, age_seconds = self.is_fresh(latest.get("checked_at"), max_age_seconds)
        return {
            "status": latest.get("status", "unknown"),
            "fresh": fresh,
            "age_seconds": age_seconds,
            "max_age_seconds": max_age_seconds,
            "checked_at": latest.get("checked_at"),
            "issue_count": len(latest.get("issues", []) or []),
            "snapshot_hash": latest.get("snapshot_hash"),
        }

    async def pending_post_submit_requirements(self, user_id: str) -> dict[str, Any]:
        pending = await self.db.live_post_submit_reconciliation_requirements.find({"user_id": user_id, "status": "pending"}, {"_id": 0}).sort("created_at", -1).limit(50).to_list(50)
        return {"pending_count": len(pending), "pending": pending}

    async def active_halts(self, user_id: str) -> dict[str, Any]:
        halts = await self.db.live_halts.find({"active": True}, {"_id": 0}).sort("created_at", -1).limit(50).to_list(50)
        scoped = [halt for halt in halts if halt.get("scope") == "global" or halt.get("user_id") in {None, user_id}]
        return {"active_count": len(scoped), "halts": scoped}

    async def checklist(self, user_id: str) -> dict[str, Any]:
        mode = TradingModeService().describe()
        gate = self.gate.describe()
        live_readonly_status = await self.live_readonly.latest_snapshot_status(user_id)
        reconciliation = await self.latest_reconciliation_status(user_id)
        pending_reconciliation = await self.pending_post_submit_requirements(user_id)
        halts = await self.active_halts(user_id)
        adapter_kill_switch = CoinbaseLiveExecutionAdapterV2.live_order_kill_switch_enabled()

        checks = [
            {
                "name": "autonomous_live_disabled",
                "passed": True,
                "severity": "critical",
                "detail": "autonomous bot execution remains paper-only; manual live path is separate",
            },
            {
                "name": "trading_mode_live_trading",
                "passed": mode.get("mode") == "live-trading",
                "severity": "critical",
                "detail": "TRADING_MODE must be live-trading for a non-dry-run manual live pilot",
                "observed": mode.get("mode"),
            },
            {
                "name": "global_live_gate_enabled",
                "passed": bool(gate.get("live_trading_enabled")),
                "severity": "critical",
                "detail": "LIVE_TRADING_ENABLED must be true for the pilot window only",
            },
            {
                "name": "execution_adapter_configured",
                "passed": gate.get("execution_adapter") == gate.get("required_adapter"),
                "severity": "critical",
                "detail": "LIVE_EXECUTION_ADAPTER must match the required adapter",
                "observed": gate.get("execution_adapter"),
                "required": gate.get("required_adapter"),
            },
            {
                "name": "manual_approval_required",
                "passed": bool(gate.get("manual_approval_required")),
                "severity": "critical",
                "detail": "manual approval must remain required for every non-dry-run pilot order",
            },
            {
                "name": "signed_approval_required",
                "passed": bool(gate.get("signed_approval_required")),
                "severity": "critical",
                "detail": "signed approval challenges should remain required for pilot orders",
            },
            {
                "name": "live_readonly_snapshot_fresh",
                "passed": bool(live_readonly_status.get("fresh")),
                "severity": "critical",
                "detail": "latest live-readonly snapshot must be fresh",
                "observed": live_readonly_status,
            },
            {
                "name": "live_readonly_reconciliation_fresh_and_ok",
                "passed": reconciliation.get("fresh") is True and reconciliation.get("status") == "ok" and reconciliation.get("issue_count", 1) == 0,
                "severity": "critical",
                "detail": "latest live-readonly reconciliation must be fresh, ok, and issue-free",
                "observed": reconciliation,
            },
            {
                "name": "no_pending_post_submit_reconciliation",
                "passed": pending_reconciliation.get("pending_count") == 0,
                "severity": "critical",
                "detail": "all previous live order reconciliation requirements must be resolved before another pilot order",
                "observed": {"pending_count": pending_reconciliation.get("pending_count")},
            },
            {
                "name": "no_active_halts",
                "passed": halts.get("active_count") == 0,
                "severity": "critical",
                "detail": "no active global or user live halt may exist during the pilot submit window",
                "observed": {"active_count": halts.get("active_count")},
            },
            {
                "name": "adapter_kill_switch_open_for_submit_window",
                "passed": not adapter_kill_switch,
                "severity": "critical",
                "detail": "COINBASE_LIVE_ORDER_KILL_SWITCH must be false only during the intentional submit window",
                "observed": {"kill_switch_enabled": adapter_kill_switch},
            },
            {
                "name": "tiny_order_cap",
                "passed": float(gate.get("max_order_notional_usd", 999999.0)) <= 25.0,
                "severity": "critical",
                "detail": "manual pilot max order notional should remain tiny",
                "observed": gate.get("max_order_notional_usd"),
            },
        ]
        blockers = [check for check in checks if check["severity"] == "critical" and not check["passed"]]
        return {
            "user_id": user_id,
            "status": "ready" if not blockers else "not_ready",
            "ready": not blockers,
            "checked_at": self.iso(self.utc_now()),
            "blockers": blockers,
            "checks": checks,
            "mode": mode,
            "gate": gate,
            "live_readonly_status": live_readonly_status,
            "latest_reconciliation": reconciliation,
            "pending_post_submit_reconciliation": pending_reconciliation,
            "active_halts": halts,
        }
