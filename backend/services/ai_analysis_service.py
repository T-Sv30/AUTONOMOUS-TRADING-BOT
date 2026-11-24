import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.append(str(Path(__file__).parent.parent))
try:
    from emergentintegrations.llm.chat import LlmChat, UserMessage
except ImportError:
    # Local stub if directory setup fails
    class UserMessage:
        def __init__(self, text): self.text = text
    class LlmChat:
        def __init__(self, *args, **kwargs): pass
        def with_model(self, *args, **kwargs): return self
        async def send_message(self, *args, **kwargs):
            return '{"regime": "trend", "recommendation": "HOLD", "confidence": 50, "reasoning": "AI stub active", "risks": "none"}'

from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

class AIAnalysisService:
    def __init__(self):
        self.api_key = os.getenv('EMERGENT_LLM_KEY')
        if not self.api_key:
            raise ValueError("EMERGENT_LLM_KEY not found in environment")
    
    async def analyze_market(self, symbol: str, price_data: dict[str, Any], market_indicators: dict[str, Any]) -> dict[str, Any]:
        """Use GPT-5 to analyze market conditions and generate trading signals"""
        try:
            # Initialize GPT-5 chat
            chat = LlmChat(
                api_key=self.api_key,
                session_id=f"analysis_{symbol}_{int(datetime.now().timestamp())}",
                system_message="You are an expert cryptocurrency trading analyst. Analyze market data and provide clear, concise trading recommendations with reasoning."
            ).with_model("openai", "gpt-5")
            
            # Prepare analysis prompt
            prompt = f"""Analyze the following market data for {symbol}:

Current Price: ${price_data.get('price', 0)}
24h Change: {price_data.get('change_24h', 0)}%
Volume: {price_data.get('volume', 0)}

Market Indicators:
- Regime: {market_indicators.get('regime', 'unknown')}
- Volatility: {market_indicators.get('volatility', 'medium')}
- Trend: {market_indicators.get('trend', 'neutral')}

Provide:
1. Market regime assessment (Trend/Mean-Reversion/Volatility-Crush/Shock)
2. BUY/HOLD/SELL recommendation
3. Confidence level (0-100)
4. Brief reasoning (2-3 sentences)
5. Key risk factors

Respond in this JSON format:
{{
  "regime": "<regime>",
  "recommendation": "<BUY|HOLD|SELL>",
  "confidence": <0-100>,
  "reasoning": "<explanation>",
  "risks": "<key risks>"
}}"""
            
            message = UserMessage(text=prompt)
            response = await chat.send_message(message)
            
            # Parse response
            import json
            try:
                analysis = json.loads(response)
            except (json.JSONDecodeError, TypeError):
                # Fallback if response isn't valid JSON
                analysis = {
                    "regime": "trend",
                    "recommendation": "HOLD",
                    "confidence": 50,
                    "reasoning": response[:200],
                    "risks": "Unable to parse full analysis"
                }
            
            return {
                "symbol": symbol,
                "regime": analysis.get('regime', 'trend'),
                "signal": analysis.get('recommendation', 'HOLD'),
                "confidence": analysis.get('confidence', 50),
                "ai_analysis": analysis.get('reasoning', ''),
                "risks": analysis.get('risks', ''),
                "buy_recommendation": analysis.get('recommendation') == 'BUY',
                "timestamp": datetime.now(UTC).isoformat()
            }
            
        except Exception as e:
            logger.error(f"AI analysis error: {e}")
            # Return safe fallback
            return {
                "symbol": symbol,
                "regime": "neutral",
                "signal": "HOLD",
                "confidence": 0,
                "ai_analysis": f"Analysis temporarily unavailable: {str(e)}",
                "risks": "System error",
                "buy_recommendation": False,
                "timestamp": datetime.now(UTC).isoformat()
            }
