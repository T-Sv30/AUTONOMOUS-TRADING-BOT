from services.advanced_risk_manager import AdvancedRiskManager


def test_heat_counts_dollars_at_risk():
    heat = AdvancedRiskManager().calculate_portfolio_heat(
        [{"quantity": 0.002, "current_price": 50000.0, "avg_price": 49000.0}], total_equity=10000.0)
    assert heat["total_heat"] == 3.0          # 0.002 * 50,000 * 3%
    assert heat["heat_percent"] == 0.03
