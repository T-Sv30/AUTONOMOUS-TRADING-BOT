import pytest

from services.phase17_autonomous_shadow_mode_service_v2 import Phase17AutonomousShadowModeServiceV2


def valid_payload(**overrides):
    payload = {
        "mode": "shadow",
        "submit_live_order": False,
        "autonomous_execution_enabled": False,
        "strategy": "trend_following",
        "symbol": "BTC-USD",
        "side": "BUY",
        "confidence": 0.75,
        "market_data_age_seconds": 10,
        "notional_usd": 5.0,
        "reference_price": 50000.0,
        "slippage_bps": 5.0,
        "risk_decision": {"decision": "allow"},
        "halt_assumption": {"halted": False},
        "reconciliation_assumption": {"required": False, "mode": "shadow"},
    }
    payload.update(overrides)
    return payload


def valid_summary(**overrides):
    summary = {
        "shadow_days": 14,
        "decision_count": 50,
        "would_trade_count": 3,
        "error_count": 0,
        "max_simulated_drawdown_pct": 1.0,
        "simulated_pnl_usd": 2.5,
        "halt_count": 0,
        "reconciliation_issue_count": 0,
    }
    summary.update(overrides)
    return summary


def names(result):
    return {item["name"] for item in result["blockers"]}


def test_phase17_policy_is_shadow_only():
    policy = Phase17AutonomousShadowModeServiceV2.policy()

    assert policy["release_scope"] == "shadow_mode_only_no_execution"
    assert policy["live_order_submission"] == "not_allowed"
    assert policy["autonomous_execution_enabled"] is False


def test_phase17_valid_input_builds_would_trade_shadow_decision_without_submission():
    decision = Phase17AutonomousShadowModeServiceV2.build_shadow_decision(valid_payload())

    assert decision["mode"] == "shadow"
    assert decision["would_submit_live_order"] is False
    assert decision["would_trade"] is True
    assert decision["simulated_quantity"] > 0
    assert decision["decision_hash"]
    assert decision["validation"]["valid_shadow_decision_input"] is True


def test_phase17_hold_decision_can_be_valid_below_confidence_threshold():
    result = Phase17AutonomousShadowModeServiceV2.validate_input(
        valid_payload(side="HOLD", confidence=0.1, notional_usd=0.0)
    )

    assert result["valid_shadow_decision_input"] is True
    assert result["blockers"] == []


def test_phase17_blocks_live_submission_and_bad_identity_inputs():
    result = Phase17AutonomousShadowModeServiceV2.validate_input(
        valid_payload(
            mode="live",
            submit_live_order=True,
            autonomous_execution_enabled=True,
            strategy="unknown",
            symbol="DOGE-USD",
        )
    )

    blockers = names(result)
    assert "shadow_mode_explicit" in blockers
    assert "live_order_submission_disabled" in blockers
    assert "strategy_allowlisted" in blockers
    assert "symbol_allowlisted" in blockers


def test_phase17_blocks_weak_signal_stale_data_bad_size_and_missing_artifacts():
    result = Phase17AutonomousShadowModeServiceV2.validate_input(
        valid_payload(
            confidence=0.1,
            market_data_age_seconds=120,
            notional_usd=50.0,
            risk_decision={},
            halt_assumption=None,
            reconciliation_assumption=None,
        )
    )

    blockers = names(result)
    assert "confidence_threshold_met_or_hold" in blockers
    assert "market_data_fresh_enough" in blockers
    assert "shadow_notional_limited_or_hold" in blockers
    assert "risk_decision_present" in blockers
    assert "halt_and_reconciliation_assumptions_present" in blockers


def test_phase17_valid_window_summary_passes_review():
    result = Phase17AutonomousShadowModeServiceV2.evaluate_shadow_window(valid_summary())

    assert result["status"] == "shadow_review_passed"
    assert result["ready_for_autonomous_canary_review"] is True
    assert result["autonomous_execution_enabled"] is False
    assert result["blockers"] == []


def test_phase17_window_review_blocks_insufficient_evidence_and_issues():
    result = Phase17AutonomousShadowModeServiceV2.evaluate_shadow_window(
        valid_summary(
            shadow_days=1,
            decision_count=5,
            would_trade_count=0,
            error_count=5,
            max_simulated_drawdown_pct=10.0,
            halt_count=1,
            reconciliation_issue_count=1,
        )
    )

    blockers = names(result)
    assert "minimum_shadow_duration_met" in blockers
    assert "minimum_decision_count_met" in blockers
    assert "would_trade_decisions_observed" in blockers
    assert "simulated_drawdown_within_limit" in blockers
    assert "error_rate_within_limit" in blockers
    assert "no_halts_or_reconciliation_issues" in blockers


@pytest.mark.parametrize("strategy", ["trend_following", "mean_reversion", "breakout"])
def test_phase17_accepts_every_strategy_the_bot_runs(strategy):
    result = Phase17AutonomousShadowModeServiceV2.validate_input(valid_payload(strategy=strategy))

    assert "strategy_allowlisted" not in names(result)
    assert result["valid_shadow_decision_input"] is True


def test_phase17_rejects_the_retired_ma_cross_strategy():
    result = Phase17AutonomousShadowModeServiceV2.validate_input(valid_payload(strategy="ma_cross_risk_managed_v1"))

    assert "strategy_allowlisted" in names(result)
    assert result["valid_shadow_decision_input"] is False


def test_phase17_allowlist_is_the_ensemble_strategy_set():
    from services.strategy_ensemble_v2 import StrategyEnsembleV2

    names_run = {signal["strategy"] for signal in StrategyEnsembleV2().generate_all([100.0 + i for i in range(120)], has_position=False)}
    assert set(Phase17AutonomousShadowModeServiceV2.policy()["allowed_strategies"]) == names_run
