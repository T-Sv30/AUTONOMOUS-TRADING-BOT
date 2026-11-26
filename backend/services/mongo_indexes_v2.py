from typing import Any


class MongoIndexServiceV2:
    """Creates operational indexes for core trading collections."""

    def __init__(self, db):
        self.db = db

    async def ensure_indexes(self) -> dict[str, Any]:
        created: list[str] = []

        async def create(collection, keys, **kwargs):
            name = await collection.create_index(keys, **kwargs)
            created.append(name)

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
        await create(self.db.live_approval_challenges, [("expires_at", 1)], expireAfterSeconds=3600, name="live_approval_expiry_ttl")
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
        await create(self.db.auth_failures, [("created_at", 1)], expireAfterSeconds=3600, name="auth_failures_ttl")

        return {"status": "ok", "created_or_verified": created}
