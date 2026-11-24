import logging
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

class PerformanceAnalyzer:
    """Advanced performance analytics: Sharpe, Sortino, Calmar, etc."""
    
    def calculate_sharpe_ratio(self, returns: list[float], risk_free_rate: float = 0.02) -> float:
        """Calculate Sharpe Ratio"""
        if len(returns) < 2:
            return 0.0
        
        returns_array = np.array(returns)
        excess_returns = returns_array - (risk_free_rate / 252)  # Daily risk-free rate
        
        if np.std(excess_returns) == 0:
            return 0.0
        
        sharpe = np.mean(excess_returns) / np.std(excess_returns) * np.sqrt(252)
        return round(float(sharpe), 2)
    
    def calculate_sortino_ratio(self, returns: list[float], risk_free_rate: float = 0.02) -> float:
        """Calculate Sortino Ratio (only considers downside volatility)"""
        if len(returns) < 2:
            return 0.0
        
        returns_array = np.array(returns)
        excess_returns = returns_array - (risk_free_rate / 252)
        
        # Only negative returns
        downside_returns = excess_returns[excess_returns < 0]
        
        if len(downside_returns) == 0 or np.std(downside_returns) == 0:
            return 0.0
        
        sortino = np.mean(excess_returns) / np.std(downside_returns) * np.sqrt(252)
        return round(float(sortino), 2)
    
    def calculate_calmar_ratio(self, returns: list[float], max_drawdown: float) -> float:
        """Calculate Calmar Ratio (return / max drawdown)"""
        if max_drawdown == 0:
            return 0.0
        
        annualized_return = np.mean(returns) * 252 if returns else 0
        calmar = annualized_return / abs(max_drawdown) if max_drawdown != 0 else 0
        
        return round(float(calmar), 2)
    
    def calculate_max_consecutive_wins_losses(self, trades: list[dict[str, Any]]) -> dict[str, int]:
        """Calculate maximum consecutive wins and losses"""
        if not trades:
            return {'max_wins': 0, 'max_losses': 0}
        
        max_wins = 0
        max_losses = 0
        current_wins = 0
        current_losses = 0
        
        for trade in trades:
            pnl = trade.get('pnl', 0)
            
            if pnl > 0:
                current_wins += 1
                current_losses = 0
                max_wins = max(max_wins, current_wins)
            elif pnl < 0:
                current_losses += 1
                current_wins = 0
                max_losses = max(max_losses, current_losses)
        
        return {
            'max_consecutive_wins': max_wins,
            'max_consecutive_losses': max_losses
        }
    
    def calculate_expectancy(self, trades: list[dict[str, Any]]) -> float:
        """Calculate trading expectancy (average $ won per trade)"""
        if not trades:
            return 0.0
        
        winning_trades = [t for t in trades if t.get('pnl', 0) > 0]
        losing_trades = [t for t in trades if t.get('pnl', 0) < 0]
        
        if not trades:
            return 0.0
        
        win_rate = len(winning_trades) / len(trades)
        avg_win = np.mean([t['pnl'] for t in winning_trades]) if winning_trades else 0
        avg_loss = abs(np.mean([t['pnl'] for t in losing_trades])) if losing_trades else 0
        
        expectancy = (win_rate * avg_win) - ((1 - win_rate) * avg_loss)
        
        return round(float(expectancy), 2)
    
    def calculate_ulcer_index(self, equity_curve: list[float]) -> float:
        """Calculate Ulcer Index (measure of downside volatility)"""
        if len(equity_curve) < 2:
            return 0.0
        
        drawdowns = []
        peak = equity_curve[0]
        
        for value in equity_curve:
            if value > peak:
                peak = value
            
            dd_pct = ((peak - value) / peak * 100) if peak > 0 else 0
            drawdowns.append(dd_pct ** 2)
        
        ulcer = np.sqrt(np.mean(drawdowns))
        return round(float(ulcer), 2)
    
    async def get_comprehensive_analysis(self, trades: list[dict[str, Any]], 
                                          equity_history: list[float]) -> dict[str, Any]:
        """Get comprehensive performance analysis"""
        
        if not trades or not equity_history:
            return self._default_analysis()
        
        # Calculate returns
        returns = []
        for i in range(1, len(equity_history)):
            ret = (equity_history[i] - equity_history[i-1]) / equity_history[i-1]
            returns.append(ret)
        
        # Winning/Losing stats
        winning_trades = [t for t in trades if t.get('pnl', 0) > 0]
        losing_trades = [t for t in trades if t.get('pnl', 0) < 0]
        
        # Max drawdown
        max_dd = 0
        peak = equity_history[0]
        for value in equity_history:
            if value > peak:
                peak = value
            dd = (peak - value) / peak * 100 if peak > 0 else 0
            max_dd = max(max_dd, dd)
        
        # Ratios
        sharpe = self.calculate_sharpe_ratio(returns)
        sortino = self.calculate_sortino_ratio(returns)
        calmar = self.calculate_calmar_ratio(returns, max_dd)
        
        # Trade stats
        consecutive = self.calculate_max_consecutive_wins_losses(trades)
        expectancy = self.calculate_expectancy(trades)
        ulcer = self.calculate_ulcer_index(equity_history)
        
        # Best/Worst trades
        best_trade = max(trades, key=lambda t: t.get('pnl', 0)) if trades else None
        worst_trade = min(trades, key=lambda t: t.get('pnl', 0)) if trades else None
        
        return {
            'sharpe_ratio': sharpe,
            'sortino_ratio': sortino,
            'calmar_ratio': calmar,
            'max_drawdown': round(max_dd, 2),
            'ulcer_index': ulcer,
            'expectancy': expectancy,
            'max_consecutive_wins': consecutive['max_consecutive_wins'],
            'max_consecutive_losses': consecutive['max_consecutive_losses'],
            'total_trades': len(trades),
            'win_rate': round(len(winning_trades) / len(trades) * 100, 2) if trades else 0,
            'avg_win': round(np.mean([t['pnl'] for t in winning_trades]), 2) if winning_trades else 0,
            'avg_loss': round(np.mean([t['pnl'] for t in losing_trades]), 2) if losing_trades else 0,
            'best_trade': {
                'symbol': best_trade.get('symbol'),
                'pnl': round(best_trade.get('pnl', 0), 2),
                'date': best_trade.get('created_at')
            } if best_trade else None,
            'worst_trade': {
                'symbol': worst_trade.get('symbol'),
                'pnl': round(worst_trade.get('pnl', 0), 2),
                'date': worst_trade.get('created_at')
            } if worst_trade else None
        }
    
    def _default_analysis(self) -> dict[str, Any]:
        """Return default analysis when not enough data"""
        return {
            'sharpe_ratio': 0.0,
            'sortino_ratio': 0.0,
            'calmar_ratio': 0.0,
            'max_drawdown': 0.0,
            'ulcer_index': 0.0,
            'expectancy': 0.0,
            'max_consecutive_wins': 0,
            'max_consecutive_losses': 0,
            'total_trades': 0,
            'win_rate': 0.0,
            'avg_win': 0.0,
            'avg_loss': 0.0,
            'best_trade': None,
            'worst_trade': None
        }
