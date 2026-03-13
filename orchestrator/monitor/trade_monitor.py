import os
import logging
import asyncio
from typing import Any
from ib_insync import IB, MarketOrder, LimitOrder, util

logger = logging.getLogger(__name__)

IB_HOST = os.getenv("IB_HOST", "127.0.0.1")
IB_PORT = int(os.getenv("IB_PORT", "4001"))
IB_CLIENT_ID = int(os.getenv("IB_CLIENT_ID", "1"))


class TradeMonitor:
    """
    Wraps ib_insync to connect to IB Gateway, place orders,
    and provide live position data.
    """

    def __init__(self):
        self._ib = IB()
        self._connected = False

    async def connect(self):
        try:
            await self._ib.connectAsync(IB_HOST, IB_PORT, clientId=IB_CLIENT_ID)
            self._connected = True
            logger.info(f"Connected to IB Gateway at {IB_HOST}:{IB_PORT}")
        except Exception as e:
            self._connected = False
            logger.error(f"Failed to connect to IB Gateway: {e}")

    async def disconnect(self):
        if self._connected:
            self._ib.disconnect()
            self._connected = False
            logger.info("Disconnected from IB Gateway.")

    def is_connected(self) -> bool:
        return self._connected and self._ib.isConnected()

    async def place_order(
        self,
        symbol: str,
        side: str,
        quantity: int,
        order_type: str = "MKT",
        limit_price: float | None = None,
    ) -> str:
        """
        Places an order via IB Gateway. Returns the IB order ID as a string.
        Raises on failure.
        """
        if not self.is_connected():
            raise RuntimeError("IB Gateway not connected")

        from ib_insync import Stock, Future, Contract

        # Simple contract resolution — extend this for futures, forex, etc.
        contract = Stock(symbol, "SMART", "USD")
        await self._ib.qualifyContractsAsync(contract)

        if order_type == "MKT":
            ib_order = MarketOrder(side, quantity)
        elif order_type == "LMT":
            if limit_price is None:
                raise ValueError("limit_price required for LMT orders")
            ib_order = LimitOrder(side, quantity, limit_price)
        else:
            raise ValueError(f"Unsupported order type: {order_type}")

        trade = self._ib.placeOrder(contract, ib_order)
        logger.info(f"Order placed: {side} {quantity} {symbol} [{order_type}] → permId={trade.order.permId}")
        return str(trade.order.orderId)

    async def get_positions(self) -> list[dict[str, Any]]:
        """Returns current open positions from IB."""
        if not self.is_connected():
            return []
        positions = await self._ib.reqPositionsAsync()
        return [
            {
                "account": p.account,
                "symbol": p.contract.symbol,
                "position": p.position,
                "avg_cost": p.avgCost,
            }
            for p in positions
        ]
