from services.phase12_tiny_manual_live_pilot_service_v2 import Phase12TinyManualLivePilotServiceV2

ACK = Phase12TinyManualLivePilotServiceV2.REQUIRED_ACKNOWLEDGEMENT


def readiness():
    return {"ready": True, "status": "ready", "blockers": []}


def expansion():
    return {"allowed_to_repeat_pilot": True, "blockers": []}


def active_state():
    return {
        "pending_reconciliation_count": 0,
        "pending_reconciliation": [],
        "unsigned_completed_report_count": 0,
        "unsigned_completed_reports": [],
    }


def dry_run(symbol="BTC-USD", side="BUY", notional=5.0):
    return {
        "status": "dry_run",
        "live_order_id": "dry-run-order-1",
        "order": {
            "status": "dry_run",
            "live_execution": False,
            "requested": {
                "symbol": symbol,
                "side": side,
                "notional_usd": notional,
            },
        },
    }


def validate(**overrides):
    payload = {
        "symbol": "BTC-USD",
        "side": "BUY",
        "notional_usd": 5.0,
        "operator_acknowledgement": ACK,
        "readiness": readiness(),
        "expansion_status": expansion(),
        "latest_dry_run": dry_run(),
        "active_state": active_state(),
        "max_notional_usd": 5.0,
    }
    payload.update(overrides)
    return Phase12TinyManualLivePilotServiceV2.validate_candidate(**payload)


def names(result):
    return {item["name"] for item in result["blockers"]}


def test_phase12_plan_is_one_order_human_triggered_only():
    plan = Phase12TinyManualLivePilotServiceV2.plan()

    assert plan["max_orders"] == 1
    assert plan["live_order_submission"] == "human_triggered_only_existing_manual_endpoint"
    assert "restore_kill_switch" in plan["mandatory_post_submit_steps"]
    assert "stop_after_one_order" in plan["mandatory_post_submit_steps"]


def test_phase12_valid_candidate_is_eligible():
    result = validate()

    assert result["status"] == "eligible"
    assert result["eligible_for_one_tiny_manual_live_pilot"] is True
    assert result["blockers"] == []


def test_phase12_requires_exact_operator_acknowledgement():
    result = validate(operator_acknowledgement="ack")

    assert result["status"] == "blocked"
    assert "operator_acknowledgement_exact" in names(result)


def test_phase12_blocks_non_allowlisted_symbol():
    result = validate(symbol="DOGE-USD", latest_dry_run=dry_run(symbol="DOGE-USD"))

    assert "symbol_allowlisted_for_first_pilot" in names(result)


def test_phase12_blocks_large_notional():
    result = validate(notional_usd=50.0, latest_dry_run=dry_run(notional=50.0), max_notional_usd=50.0)

    assert "notional_is_positive_and_tiny" in names(result)


def test_phase12_blocks_readiness_failure():
    result = validate(readiness={"ready": False, "status": "not_ready", "blockers": [{"name": "x"}]})

    assert "pilot_readiness_clear" in names(result)


def test_phase12_blocks_pending_reconciliation():
    blocked_state = active_state()
    blocked_state["pending_reconciliation_count"] = 1
    result = validate(active_state=blocked_state)

    assert "no_pending_reconciliation_or_unsigned_reports" in names(result)


def test_phase12_blocks_dry_run_mismatch():
    result = validate(latest_dry_run=dry_run(symbol="ETH-USD"))

    assert "latest_dry_run_matches_candidate_order" in names(result)


def test_phase12_blocks_any_live_execution_signal_in_dry_run_artifact():
    artifact = dry_run()
    artifact["order"]["live_execution"] = True
    result = validate(latest_dry_run=artifact)

    assert "latest_dry_run_not_live_execution" in names(result)
