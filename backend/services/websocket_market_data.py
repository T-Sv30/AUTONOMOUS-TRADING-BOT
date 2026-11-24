import asyncio
import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import websockets

logger = logging.getLogger(__name__)

class WebSocketMarketData:
    """Real-time market data via WebSocket"""
    
    def __init__(self):
        self.ws_url = "wss://ws-feed.exchange.coinbase.com"
        self.connections = {}
        self.subscribers = {}  # symbol -> list of callbacks
        self.running = False
        self.price_cache = {}  # Latest prices
    
    async def start(self, symbols: list):
        """Start WebSocket connections for given symbols"""
        self.running = True
        logger.info(f"Starting WebSocket for symbols: {symbols}")
        
        for symbol in symbols:
            asyncio.create_task(self._connect_symbol(symbol))
    
    async def _connect_symbol(self, symbol: str):
        """Maintain WebSocket connection for a symbol"""
        while self.running:
            try:
                async with websockets.connect(self.ws_url) as websocket:
                    # Subscribe to ticker channel
                    subscribe_message = {
                        "type": "subscribe",
                        "product_ids": [symbol],
                        "channels": ["ticker"]
                    }
                    await websocket.send(json.dumps(subscribe_message))
                    logger.info(f"Subscribed to {symbol} ticker")
                    
                    # Store connection
                    self.connections[symbol] = websocket
                    
                    # Listen for messages
                    async for message in websocket:
                        if not self.running:
                            break
                        
                        try:
                            data = json.loads(message)
                            await self._handle_message(symbol, data)
                        except json.JSONDecodeError:
                            logger.error(f"Failed to parse message: {message}")
                        except Exception as e:
                            logger.error(f"Error handling message: {e}")
                            
            except websockets.exceptions.WebSocketException as e:
                logger.error(f"WebSocket error for {symbol}: {e}")
                await asyncio.sleep(5)  # Reconnect after 5 seconds
            except Exception as e:
                logger.error(f"Unexpected error for {symbol}: {e}")
                await asyncio.sleep(5)
    
    async def _handle_message(self, symbol: str, data: dict[str, Any]):
        """Process incoming WebSocket message"""
        if data.get('type') == 'ticker':
            # Update price cache
            price_update = {
                'symbol': symbol,
                'price': float(data.get('price', 0)),
                'volume_24h': float(data.get('volume_24h', 0)),
                'best_bid': float(data.get('best_bid', 0)),
                'best_ask': float(data.get('best_ask', 0)),
                'timestamp': datetime.now(UTC).isoformat()
            }
            
            self.price_cache[symbol] = price_update
            
            # Notify subscribers
            if symbol in self.subscribers:
                for callback in self.subscribers[symbol]:
                    try:
                        await callback(price_update)
                    except Exception as e:
                        logger.error(f"Error in subscriber callback: {e}")
    
    def subscribe(self, symbol: str, callback: Callable):
        """Subscribe to price updates for a symbol"""
        if symbol not in self.subscribers:
            self.subscribers[symbol] = []
        self.subscribers[symbol].append(callback)
        logger.info(f"Added subscriber for {symbol}")
    
    def get_latest_price(self, symbol: str) -> dict[str, Any]:
        """Get latest cached price for a symbol"""
        return self.price_cache.get(symbol, {})
    
    async def stop(self):
        """Stop all WebSocket connections"""
        self.running = False
        
        for symbol, ws in self.connections.items():
            try:
                await ws.close()
                logger.info(f"Closed WebSocket for {symbol}")
            except Exception as exc:
                logger.debug("Ignoring error while closing WebSocket for %s: %s", symbol, exc)
        
        self.connections.clear()
        logger.info("WebSocket service stopped")
