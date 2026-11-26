import logging
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

class AdvancedRiskManager:
    """Advanced risk management with CVaR, correlation, and portfolio heat"""
    
    def __init__(self):
        self.var_confidence = 0.95  # 95% confidence for VaR
        self.max_correlation = 0.7  # Max allowed correlation between positions
        self.max_portfolio_heat = 0.15  # Max 15% of portfolio at risk
    
    def calculate_cvar(self, returns: list[float], confidence: float = 0.95) -> float:
        """Calculate Conditional Value at Risk (CVaR/Expected Shortfall)"""
        if len(returns) < 10:
            return 0.0
        
        try:
            returns_array = np.array(returns)
            
            # Calculate VaR (Value at Risk)
            var = np.percentile(returns_array, (1 - confidence) * 100)
            
            # CVaR is the expected loss beyond VaR
            cvar = returns_array[returns_array <= var].mean()
            
            return round(float(cvar), 4)
        except Exception as e:
            logger.error(f"Error calculating CVaR: {e}")
            return 0.0
    
    def calculate_portfolio_heat(self, positions: list[dict[str, Any]], 
                                  total_equity: float) -> dict[str, Any]:
        """Calculate portfolio heat (total risk exposure)"""
        if not positions or total_equity <= 0:
            return {
                'total_heat': 0.0,
                'heat_percent': 0.0,
                'positions_at_risk': 0,
                'status': 'safe'
            }
        
        try:
            # Calculate total amount at risk (from entry to stop loss)
            total_risk = 0
            positions_at_risk = 0
            
            for pos in positions:
                quantity = pos.get('quantity', 0)
                current_price = pos.get('current_price')
                avg_price = pos.get('avg_price')
                if current_price and current_price > 0:
                    price = current_price
                elif avg_price:
                    price = avg_price
                else:
                    price = 0
                
                # Assume 3% stop loss for heat calculation (dollars at risk)
                stop_loss_distance = 0.03
                position_risk = quantity * price * stop_loss_distance
                
                total_risk += position_risk
                positions_at_risk += 1
            
            heat_percent = (total_risk / total_equity * 100) if total_equity > 0 else 0
            
            # Determine status
            if heat_percent > 15:
                status = 'high_risk'
            elif heat_percent > 10:
                status = 'elevated'
            elif heat_percent > 5:
                status = 'moderate'
            else:
                status = 'safe'
            
            return {
                'total_heat': round(total_risk, 2),
                'heat_percent': round(heat_percent, 2),
                'positions_at_risk': positions_at_risk,
                'status': status
            }
        except Exception as e:
            logger.error(f"Error calculating portfolio heat: {e}")
            return {
                'total_heat': 0.0,
                'heat_percent': 0.0,
                'positions_at_risk': 0,
                'status': 'unknown'
            }
    
    def calculate_correlation(self, prices_a: list[float], prices_b: list[float]) -> float:
        """Calculate correlation between two price series"""
        if len(prices_a) < 20 or len(prices_b) < 20:
            return 0.0
        
        try:
            # Use last N periods
            n = min(len(prices_a), len(prices_b), 50)
            a = np.array(prices_a[-n:])
            b = np.array(prices_b[-n:])
            
            # Calculate returns
            returns_a = np.diff(a) / a[:-1]
            returns_b = np.diff(b) / b[:-1]
            
            # Correlation
            correlation = np.corrcoef(returns_a, returns_b)[0, 1]
            
            return round(float(correlation), 3)
        except Exception as e:
            logger.error(f"Error calculating correlation: {e}")
            return 0.0
    
    def check_correlation_risk(self, new_symbol: str, existing_positions: list[dict[str, Any]], 
                               price_history: dict[str, list[float]]) -> dict[str, Any]:
        """Check if adding new position would create correlation risk"""
        if not existing_positions:
            return {
                'allowed': True,
                'max_correlation': 0.0,
                'correlated_with': None
            }
        
        new_prices = price_history.get(new_symbol, [])
        if len(new_prices) < 20:
            return {'allowed': True, 'max_correlation': 0.0, 'correlated_with': None}
        
        max_corr = 0.0
        correlated_with = None
        
        for pos in existing_positions:
            symbol = pos.get('symbol')
            if symbol == new_symbol:
                continue
            
            existing_prices = price_history.get(symbol, [])
            if len(existing_prices) < 20:
                continue
            
            corr = self.calculate_correlation(new_prices, existing_prices)
            
            if abs(corr) > abs(max_corr):
                max_corr = corr
                correlated_with = symbol
        
        allowed = abs(max_corr) < self.max_correlation
        
        return {
            'allowed': allowed,
            'max_correlation': max_corr,
            'correlated_with': correlated_with
        }
    
    def calculate_optimal_position_size(self, signal_strength: float, volatility: float,
                                        total_equity: float, current_heat: float,
                                        max_heat: float = 0.15) -> float:
        """Calculate optimal position size considering portfolio heat"""
        # Base Kelly calculation
        edge = signal_strength
        kelly_fraction = edge / (volatility ** 2) if volatility > 0 else 0
        kelly_fraction = min(kelly_fraction * 0.25, 0.02)  # Conservative Kelly
        
        # Adjust for portfolio heat
        heat_available = max_heat - current_heat
        if heat_available <= 0:
            return 0.0  # No room for new positions
        
        # Scale down if portfolio heat is elevated
        heat_adjustment = min(heat_available / max_heat, 1.0)
        
        position_size = total_equity * kelly_fraction * heat_adjustment
        
        # Minimum $10, maximum 5% of equity
        position_size = max(10, min(position_size, total_equity * 0.05))
        
        return round(position_size, 2)
    
    def get_risk_assessment(self, equity_metrics: dict[str, float], 
                           positions: list[dict[str, Any]],
                           recent_trades: list[dict[str, Any]]) -> dict[str, Any]:
        """Comprehensive risk assessment"""
        total_equity = equity_metrics.get('total_equity', 10000)
        max_equity = equity_metrics.get('max_equity', 10000)
        daily_pnl = equity_metrics.get('daily_pnl', 0)
        
        # Portfolio heat
        heat = self.calculate_portfolio_heat(positions, total_equity)
        
        # Recent performance for CVaR
        recent_returns = [t.get('pnl_percent', 0) for t in recent_trades[-20:] if 'pnl_percent' in t]
        cvar = self.calculate_cvar(recent_returns) if recent_returns else 0.0
        
        # Drawdown
        drawdown = ((max_equity - total_equity) / max_equity * 100) if max_equity > 0 else 0
        
        # Risk score (0-100, higher = riskier)
        risk_score = (
            (heat['heat_percent'] / 15 * 30) +  # Heat contributes 30 points
            (abs(drawdown) / 10 * 30) +  # Drawdown contributes 30 points
            (abs(cvar) * 40 if cvar < 0 else 0) +  # CVaR contributes 40 points
            (abs(daily_pnl) / total_equity * 100 * 20 if daily_pnl < 0 else 0)  # Daily loss 20 points
        )
        
        risk_score = min(risk_score, 100)
        
        # Overall assessment
        if risk_score < 30:
            assessment = 'low'
        elif risk_score < 60:
            assessment = 'moderate'
        elif risk_score < 80:
            assessment = 'elevated'
        else:
            assessment = 'high'
        
        return {
            'risk_score': round(risk_score, 2),
            'assessment': assessment,
            'portfolio_heat': heat,
            'cvar': cvar,
            'drawdown': round(drawdown, 2),
            'daily_pnl': daily_pnl,
            'allow_new_positions': risk_score < 75 and heat['heat_percent'] < 15
        }
