from typing import Any


class LiveSubmitPolicyError(RuntimeError):
    pass


class LiveSubmitPolicyV2:
    """No-blind-retry policy for live order submission.

    Live POST/order submission must never be retried blindly after an ambiguous
    network result. Recovery must use idempotent exchange reads by client order
    id before any further submission decision.
    """

    @staticmethod
    def assert_no_blind_retry(*, previous_attempt: dict[str, Any] | None, client_order_id: str) -> None:
        if not previous_attempt:
            return
        status = previous_attempt.get("status")
        if status in {"submitted", "acknowledged", "partially_filled", "filled", "reconciliation_pending"}:
            raise LiveSubmitPolicyError(f"client_order_id {client_order_id} already has non-terminal live submission state: {status}")
        if previous_attempt.get("ambiguous") is True:
            raise LiveSubmitPolicyError(f"client_order_id {client_order_id} has ambiguous submit result; recover by readonly exchange lookup first")

    @staticmethod
    def recovery_action_for_ambiguous_submit(client_order_id: str) -> dict[str, str]:
        return {
            "policy": "no_blind_retry",
            "client_order_id": client_order_id,
            "required_action": "fetch_exchange_order_by_client_order_id_then_reconcile_before_any_new_submit",
        }
