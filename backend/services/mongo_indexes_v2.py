import os
from typing import Any

from pymongo.errors import OperationFailure

# Mongo error codes for dropping an index that is not there: NamespaceNotFound, IndexNotFound.
NOTHING_TO_DROP_CODES = {26, 27}


class MongoIndexServiceV2:
    """Creates operational indexes for core trading collections.

    A server-side failure on one index (for example a new unique index over
    existing duplicates) is reported in result["errors"] and never raised, so
    an existing deployment still starts. Connection errors still raise.
    """

    DEFAULT_RISK_METRICS_RETENTION_DAYS = 30

    def __init__(self, db):
        self.db = db

    @classmethod
    def risk_metrics_retention_seconds(cls) -> int:
        try:
            days = int(os.getenv("RISK_METRICS_RETENTION_DAYS", str(cls.DEFAULT_RISK_METRICS_RETENTION_DAYS)))
        except ValueError:
            days = cls.DEFAULT_RISK_METRICS_RETENTION_DAYS
        if days <= 0:
            days = cls.DEFAULT_RISK_METRICS_RETENTION_DAYS
        return days * 86400

    async def ensure_indexes(self) -> dict[str, Any]:
        created: list[str] = []
        dropped: list[str] = []
        errors: list[dict[str, str]] = []

        async def create(collection, keys, **kwargs):
            try:
                name = await collection.create_index(keys, **kwargs)
            except OperationFailure as exc:
                errors.append({"name": kwargs.get("name"), "error": str(exc)})
                return
            created.append(name)

        async def drop(collection, name):
            try:
                await collection.drop_index(name)
            except OperationFailure as exc:
                if exc.code not in NOTHING_TO_DROP_CODES:
                    errors.append({"name": name, "error": str(exc)})
                return
            dropped.append(name)

        await create(self.db.users, [("email", 1)], unique=True, name="uniq_users_email")
        await create(self.db.users, [("id", 1)], unique=True, name="uniq_users_id")
        await create(self.db.bot_configs, [("user_id", 1)], unique=True, name="uniq_bot_config_user")
        await create(self.db.risk_metrics, [("user_id", 1), ("timestamp", -1)], name="risk_metrics_user_timestamp")
        await create(self.db.risk_metrics, [("created_at_ts", 1)], expireAfterSeconds=self.risk_metrics_retention_seconds(), name="risk_metrics_ttl")
        await create(self.db.market_candles, [("symbol", 1), ("exchange", 1), ("timeframe", 1), ("open_time", 1)], unique=True, name="uniq_market_candle")
        await create(self.db.trades_v2, [("user_id", 1), ("created_at", -1)], name="trades_user_created")
        await create(self.db.trades_v2, [("client_order_id", 1)], sparse=True, name="trades_client_order_id")
        await create(
            self.db.trades_v2,
            [("idempotency_key", 1)],
            unique=True,
            partialFilterExpression={"idempotency_claim": True},
            name="uniq_trade_idempotency_claim",
        )
        await create(self.db.positions_v2, [("user_id", 1), ("symbol", 1)], unique=True, name="uniq_position_user_symbol")
        await create(self.db.portfolio_state, [("user_id", 1)], unique=True, name="uniq_portfolio_state_user")
        await create(self.db.ledger_entries, [("user_id", 1), ("created_at", -1)], name="ledger_user_created")
        await create(self.db.ledger_entries, [("entry_id", 1)], unique=True, name="uniq_ledger_entry")
        await create(self.db.reconciliation_reports, [("user_id", 1), ("checked_at", -1)], name="reconciliation_user_checked")
        await create(self.db.live_readonly_reports, [("user_id", 1), ("snapshot.timestamp", -1)], name="live_readonly_user_timestamp")
        await create(self.db.live_readonly_reports, [("user_id", 1), ("checked_at", -1)], name="live_readonly_reports_checked")
        await create(self.db.live_readonly_snapshots, [("user_id", 1), ("created_at", -1)], name="live_readonly_snapshots_user_created")
        await create(self.db.live_readonly_snapshots, [("snapshot_hash", 1)], unique=True, sparse=True, name="uniq_live_readonly_snapshot_hash")
        await create(self.db.live_order_audits, [("user_id", 1), ("created_at", -1)], name="live_audits_user_created")
        await create(self.db.live_order_audits, [("audit_hash", 1)], unique=True, sparse=True, name="uniq_live_audit_hash")
        await create(self.db.live_order_audits, [("user_id", 1), ("previous_hash", 1)], name="live_audits_user_previous_hash")
        await create(self.db.live_approval_challenges, [("challenge_id", 1)], unique=True, name="uniq_live_approval_challenge")
        await create(self.db.live_approval_challenges, [("user_id", 1), ("created_at", -1)], name="live_approval_user_created")
        # TTL indexes only expire BSON dates; the old ones sat on ISO strings and never fired.
        await drop(self.db.live_approval_challenges, "live_approval_expiry_ttl")
        await create(self.db.live_approval_challenges, [("expires_at_ts", 1)], expireAfterSeconds=0, name="live_approval_expiry_ttl_ts")
        await create(self.db.live_order_transitions, [("order_id", 1), ("sequence", 1)], unique=True, name="uniq_live_order_transition_sequence")
        await create(self.db.live_order_transitions, [("user_id", 1), ("is_latest", 1), ("state", 1), ("created_at", -1)], name="live_order_latest_user_state")
        await create(self.db.live_order_transitions, [("transition_hash", 1)], unique=True, sparse=True, name="uniq_live_order_transition_hash")
        await create(self.db.live_post_submit_reconciliation_requirements, [("user_id", 1), ("status", 1), ("created_at", -1)], name="live_post_submit_reconciliation_user_status")
        await create(self.db.live_post_submit_reconciliation_requirements, [("live_order_id", 1)], unique=True, sparse=True, name="uniq_live_post_submit_reconciliation_order")
        await create(self.db.manual_live_pilot_reports, [("user_id", 1), ("generated_at", -1)], name="manual_live_pilot_reports_user_generated")
        await create(self.db.manual_live_pilot_reports, [("user_id", 1), ("live_order_id", 1)], unique=True, name="uniq_manual_live_pilot_report_order")
        await create(self.db.manual_live_pilot_reports, [("report_hash", 1)], unique=True, sparse=True, name="uniq_manual_live_pilot_report_hash")
        await create(self.db.manual_live_pilot_signoffs, [("user_id", 1), ("signed_at", -1)], name="manual_live_pilot_signoffs_user_signed")
        await create(self.db.manual_live_pilot_signoffs, [("user_id", 1), ("live_order_id", 1)], unique=True, name="uniq_manual_live_pilot_signoff_order")
        await create(self.db.manual_live_pilot_signoffs, [("signoff_hash", 1)], unique=True, sparse=True, name="uniq_manual_live_pilot_signoff_hash")
        await create(self.db.live_halts, [("scope", 1), ("user_id", 1), ("active", 1), ("created_at", -1)], name="live_halts_scope_user_active")
        await create(self.db.worker_heartbeats, [("worker_id", 1)], unique=True, name="uniq_worker_heartbeat")
        await create(self.db.worker_heartbeats, [("updated_at", -1)], name="worker_heartbeats_updated")
        await create(self.db.bot_ownership, [("user_id", 1)], unique=True, name="uniq_bot_ownership_user")
        await create(self.db.bot_ownership, [("expires_at", 1)], name="bot_ownership_expiry")
        await create(self.db.execution_locks, [("key", 1)], unique=True, name="uniq_execution_lock")
        await create(self.db.execution_locks, [("expires_at", 1)], name="execution_lock_expiry")
        await create(self.db.alerts, [("user_id", 1), ("created_at", -1)], name="alerts_user_created")
        await create(self.db.auth_failures, [("email", 1), ("created_at", -1)], name="auth_failures_email_created")
        await drop(self.db.auth_failures, "auth_failures_ttl")
        await create(self.db.auth_failures, [("created_at_ts", 1)], expireAfterSeconds=3600, name="auth_failures_ttl_ts")

        return {"status": "ok" if not errors else "degraded", "created_or_verified": created, "dropped": dropped, "errors": errors}
