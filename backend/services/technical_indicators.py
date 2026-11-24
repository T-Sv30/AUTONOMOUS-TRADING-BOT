import logging
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

class TechnicalIndicators:
    """Calculate technical indicators for trading analysis"""
    
    @staticmethod
    def calculate_rsi(prices: list[float], period: int = 14) -> float:
        """Calculate Relative Strength Index (RSI)"""
        if len(prices) < period + 1:
            return 50.0  # Neutral if not enough data
        
        try:
            deltas = np.diff(prices)
            gains = np.where(deltas > 0, deltas, 0)
            losses = np.where(deltas < 0, -deltas, 0)
            
            avg_gain = np.mean(gains[-period:])
            avg_loss = np.mean(losses[-period:])
            
            if avg_loss == 0:
                return 100.0
            
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))
            
            return round(rsi, 2)
        except Exception as e:
            logger.error(f"Error calculating RSI: {e}")
            return 50.0
    
    @staticmethod
    def calculate_macd(prices: list[float], fast: int = 12, slow: int = 26, signal: int = 9) -> dict[str, float]:
        """Calculate MACD (Moving Average Convergence Divergence)"""
        if len(prices) < slow + signal:
            return {"macd": 0.0, "signal": 0.0, "histogram": 0.0}
        
        try:
            prices_array = np.array(prices)
            
            # Calculate EMAs
            ema_fast = TechnicalIndicators._calculate_ema(prices_array, fast)
            ema_slow = TechnicalIndicators._calculate_ema(prices_array, slow)
            
            # MACD line
            macd_line = ema_fast - ema_slow
            
            # For signal line, we need EMA of MACD values
            # Simplified: use recent MACD trend
            macd_values = []
            if len(prices) >= slow * 2:
                for i in range(slow, len(prices)):
                    f = TechnicalIndicators._calculate_ema(prices_array[:i+1], fast)
                    s = TechnicalIndicators._calculate_ema(prices_array[:i+1], slow)
                    macd_values.append(f - s)
                
                if len(macd_values) >= signal:
                    signal_line = TechnicalIndicators._calculate_ema(np.array(macd_values), signal)
                else:
                    signal_line = macd_line
            else:
                signal_line = macd_line
            
            # Histogram
            histogram = macd_line - signal_line
            
            return {
                "macd": round(float(macd_line), 2),
                "signal": round(float(signal_line), 2),
                "histogram": round(float(histogram), 2)
            }
        except Exception as e:
            logger.error(f"Error calculating MACD: {e}")
            return {"macd": 0.0, "signal": 0.0, "histogram": 0.0}
    
    @staticmethod
    def calculate_bollinger_bands(prices: list[float], period: int = 20, std_dev: float = 2.0) -> dict[str, float]:
        """Calculate Bollinger Bands"""
        if len(prices) < period:
            current_price = prices[-1] if prices else 0
            return {
                "upper": current_price * 1.02,
                "middle": current_price,
                "lower": current_price * 0.98,
                "bandwidth": 4.0
            }
        
        try:
            prices_array = np.array(prices[-period:])
            middle = np.mean(prices_array)
            std = np.std(prices_array)
            
            upper = middle + (std_dev * std)
            lower = middle - (std_dev * std)
            bandwidth = ((upper - lower) / middle) * 100
            
            return {
                "upper": round(upper, 2),
                "middle": round(middle, 2),
                "lower": round(lower, 2),
                "bandwidth": round(bandwidth, 2)
            }
        except Exception as e:
            logger.error(f"Error calculating Bollinger Bands: {e}")
            current_price = prices[-1] if prices else 0
            return {
                "upper": current_price * 1.02,
                "middle": current_price,
                "lower": current_price * 0.98,
                "bandwidth": 4.0
            }
    
    @staticmethod
    def _calculate_ema(prices: np.ndarray, period: int) -> float:
        """Calculate Exponential Moving Average"""
        if len(prices) < period:
            return np.mean(prices)
        
        multiplier = 2 / (period + 1)
        ema = np.mean(prices[:period])
        
        for price in prices[period:]:
            ema = (price * multiplier) + (ema * (1 - multiplier))
        
        return ema
    
    @staticmethod
    def calculate_volume_profile(volumes: list[float], window: int = 20) -> dict[str, float]:
        """Calculate volume analysis"""
        if len(volumes) < window:
            avg_volume = np.mean(volumes) if volumes else 0
            return {
                "avg_volume": avg_volume,
                "volume_ratio": 1.0,
                "trend": "neutral"
            }
        
        try:
            recent_volume = np.mean(volumes[-5:])
            avg_volume = np.mean(volumes[-window:])
            volume_ratio = recent_volume / avg_volume if avg_volume > 0 else 1.0
            
            if volume_ratio > 1.5:
                trend = "increasing"
            elif volume_ratio < 0.7:
                trend = "decreasing"
            else:
                trend = "neutral"
            
            return {
                "avg_volume": round(avg_volume, 2),
                "volume_ratio": round(volume_ratio, 2),
                "trend": trend
            }
        except Exception as e:
            logger.error(f"Error calculating volume profile: {e}")
            return {
                "avg_volume": 0.0,
                "volume_ratio": 1.0,
                "trend": "neutral"
            }
    
    @staticmethod
    def detect_market_regime(prices: list[float], rsi: float, macd: dict[str, float], 
                            bollinger: dict[str, float]) -> str:
        """Detect current market regime based on technical indicators"""
        if len(prices) < 20:
            return "uncertain"
        
        try:
            current_price = prices[-1]
            
            # Trend detection
            price_trend = (prices[-1] - prices[-20]) / prices[-20] * 100
            
            # RSI analysis
            oversold = rsi < 30
            overbought = rsi > 70
            
            # MACD analysis
            macd_bullish = macd['histogram'] > 0
            macd_bearish = macd['histogram'] < 0
            
            # Bollinger Bands analysis
            bb_upper = bollinger['upper']
            bb_lower = bollinger['lower']
            near_upper = current_price > (bb_upper * 0.98)
            near_lower = current_price < (bb_lower * 1.02)
            
            # Regime determination
            if price_trend > 5 and macd_bullish and not overbought:
                return "strong_uptrend"
            elif price_trend > 2 and macd_bullish:
                return "uptrend"
            elif price_trend < -5 and macd_bearish and not oversold:
                return "strong_downtrend"
            elif price_trend < -2 and macd_bearish:
                return "downtrend"
            elif oversold and near_lower:
                return "oversold_bounce"
            elif overbought and near_upper:
                return "overbought_pullback"
            elif abs(price_trend) < 2:
                return "ranging"
            else:
                return "neutral"
                
        except Exception as e:
            logger.error(f"Error detecting market regime: {e}")
            return "uncertain"
    
    @staticmethod
    def generate_trading_signals(rsi: float, macd: dict[str, float], 
                                 bollinger: dict[str, float], 
                                 current_price: float, regime: str) -> dict[str, Any]:
        """Generate trading signals based on technical indicators"""
        signals = {
            "buy_strength": 0,
            "sell_strength": 0,
            "confidence": 0,
            "reasons": []
        }
        
        # RSI signals
        if rsi < 30:
            signals["buy_strength"] += 25
            signals["reasons"].append("RSI oversold")
        elif rsi > 70:
            signals["sell_strength"] += 25
            signals["reasons"].append("RSI overbought")
        
        # MACD signals
        if macd['histogram'] > 0 and macd['macd'] > macd['signal']:
            signals["buy_strength"] += 20
            signals["reasons"].append("MACD bullish crossover")
        elif macd['histogram'] < 0 and macd['macd'] < macd['signal']:
            signals["sell_strength"] += 20
            signals["reasons"].append("MACD bearish crossover")
        
        # Bollinger Bands signals
        if current_price < bollinger['lower']:
            signals["buy_strength"] += 15
            signals["reasons"].append("Price below lower Bollinger Band")
        elif current_price > bollinger['upper']:
            signals["sell_strength"] += 15
            signals["reasons"].append("Price above upper Bollinger Band")
        
        # Regime-based adjustments
        if regime in ["strong_uptrend", "uptrend"]:
            signals["buy_strength"] += 20
            signals["reasons"].append(f"Market in {regime}")
        elif regime in ["strong_downtrend", "downtrend"]:
            signals["sell_strength"] += 20
            signals["reasons"].append(f"Market in {regime}")
        elif regime == "oversold_bounce":
            signals["buy_strength"] += 15
            signals["reasons"].append("Oversold bounce opportunity")
        
        # Calculate overall signal
        if signals["buy_strength"] > signals["sell_strength"]:
            signals["signal"] = "BUY"
            signals["confidence"] = min(signals["buy_strength"], 100)
        elif signals["sell_strength"] > signals["buy_strength"]:
            signals["signal"] = "SELL"
            signals["confidence"] = min(signals["sell_strength"], 100)
        else:
            signals["signal"] = "HOLD"
            signals["confidence"] = 50
        
        return signals
