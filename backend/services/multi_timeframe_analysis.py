import logging
from typing import Any

import numpy as np

from services.technical_indicators import TechnicalIndicators

logger = logging.getLogger(__name__)

class MultiTimeframeAnalysis:
    """Analyze multiple timeframes for better decision making"""
    
    def __init__(self):
        self.tech = TechnicalIndicators()
    
    def analyze_timeframes(self, prices: list[float]) -> dict[str, Any]:
        """Analyze multiple timeframes from price data"""
        if len(prices) < 50:
            return self._default_analysis()
        
        try:
            # Simulate different timeframes by sampling
            tf_5m = prices[-20:]   # Last 20 periods (5 min)
            tf_15m = prices[-60::3]  # Every 3rd price (15 min)
            tf_1h = prices[-100::12]  # Every 12th price (1 hour)
            tf_4h = prices[::48]  # Every 48th (4 hour)
            
            analysis = {
                '5m': self._analyze_timeframe(tf_5m, '5m'),
                '15m': self._analyze_timeframe(tf_15m, '15m'),
                '1h': self._analyze_timeframe(tf_1h, '1h'),
                '4h': self._analyze_timeframe(tf_4h, '4h')
            }
            
            # Overall trend alignment
            analysis['alignment'] = self._calculate_alignment(analysis)
            analysis['strength'] = self._calculate_trend_strength(analysis)
            
            return analysis
            
        except Exception as e:
            logger.error(f"Error in multi-timeframe analysis: {e}")
            return self._default_analysis()
    
    def _analyze_timeframe(self, prices: list[float], timeframe: str) -> dict[str, Any]:
        """Analyze a single timeframe"""
        if len(prices) < 10:
            return {
                'trend': 'neutral',
                'strength': 0,
                'rsi': 50,
                'valid': False
            }
        
        # Calculate RSI for this timeframe
        rsi = self.tech.calculate_rsi(prices, period=min(14, len(prices)-1))
        
        # Calculate trend
        if len(prices) >= 20:
            short_ma = np.mean(prices[-10:])
            long_ma = np.mean(prices[-20:])
        else:
            short_ma = np.mean(prices[-5:])
            long_ma = np.mean(prices)
        
        trend_diff = ((short_ma - long_ma) / long_ma * 100) if long_ma > 0 else 0
        
        if trend_diff > 2:
            trend = 'bullish'
            strength = min(trend_diff, 10)
        elif trend_diff < -2:
            trend = 'bearish'
            strength = min(abs(trend_diff), 10)
        else:
            trend = 'neutral'
            strength = 0
        
        return {
            'trend': trend,
            'strength': round(strength, 2),
            'rsi': rsi,
            'valid': True
        }
    
    def _calculate_alignment(self, analysis: dict[str, Any]) -> str:
        """Calculate how well timeframes are aligned"""
        trends = []
        for tf in ['5m', '15m', '1h', '4h']:
            if analysis[tf]['valid']:
                trends.append(analysis[tf]['trend'])
        
        if not trends:
            return 'none'
        
        bullish_count = trends.count('bullish')
        bearish_count = trends.count('bearish')
        
        if bullish_count >= 3:
            return 'strong_bullish'
        elif bullish_count >= 2:
            return 'bullish'
        elif bearish_count >= 3:
            return 'strong_bearish'
        elif bearish_count >= 2:
            return 'bearish'
        else:
            return 'mixed'
    
    def _calculate_trend_strength(self, analysis: dict[str, Any]) -> float:
        """Calculate overall trend strength across timeframes"""
        strengths = []
        for tf in ['5m', '15m', '1h', '4h']:
            if analysis[tf]['valid']:
                strength = analysis[tf]['strength']
                if analysis[tf]['trend'] == 'bearish':
                    strength = -strength
                strengths.append(strength)
        
        if not strengths:
            return 0.0
        
        # Weight longer timeframes more heavily
        weights = [1, 1.5, 2, 3][:len(strengths)]
        weighted_strength = sum(s * w for s, w in zip(strengths, weights, strict=False)) / sum(weights)
        
        return round(weighted_strength, 2)
    
    def _default_analysis(self) -> dict[str, Any]:
        """Return default analysis when not enough data"""
        default_tf = {'trend': 'neutral', 'strength': 0, 'rsi': 50, 'valid': False}
        return {
            '5m': default_tf.copy(),
            '15m': default_tf.copy(),
            '1h': default_tf.copy(),
            '4h': default_tf.copy(),
            'alignment': 'none',
            'strength': 0.0
        }
    
    def get_trading_recommendation(self, mtf_analysis: dict[str, Any], 
                                    current_position: bool = False) -> dict[str, Any]:
        """Get trading recommendation based on multi-timeframe analysis"""
        alignment = mtf_analysis['alignment']
        strength = mtf_analysis['strength']
        
        recommendation = {
            'action': 'HOLD',
            'confidence': 50,
            'reasons': []
        }
        
        # Strong alignment signals
        if alignment == 'strong_bullish' and strength > 3:
            recommendation['action'] = 'BUY' if not current_position else 'HOLD'
            recommendation['confidence'] = min(70 + strength * 3, 95)
            recommendation['reasons'].append('All timeframes bullish')
        
        elif alignment == 'strong_bearish' and strength < -3:
            recommendation['action'] = 'SELL' if current_position else 'AVOID'
            recommendation['confidence'] = min(70 + abs(strength) * 3, 95)
            recommendation['reasons'].append('All timeframes bearish')
        
        # Moderate alignment
        elif alignment == 'bullish' and strength > 2:
            recommendation['action'] = 'BUY' if not current_position else 'HOLD'
            recommendation['confidence'] = 60 + strength * 2
            recommendation['reasons'].append('Multiple timeframes bullish')
        
        elif alignment == 'bearish' and strength < -2:
            recommendation['action'] = 'SELL' if current_position else 'AVOID'
            recommendation['confidence'] = 60 + abs(strength) * 2
            recommendation['reasons'].append('Multiple timeframes bearish')
        
        # Mixed signals - be cautious
        else:
            recommendation['action'] = 'HOLD'
            recommendation['confidence'] = 40
            recommendation['reasons'].append('Mixed timeframe signals - waiting for clarity')
        
        return recommendation
