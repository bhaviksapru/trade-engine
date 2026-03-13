from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel, Field
from typing import Literal
import logging

logger = logging.getLogger(__name__)
router = APIRouter()


class OrderRequest(BaseModel):
    strategy_id: str = Field(..., description="Unique identifier for the calling strategy")
    symbol: str = Field(..., description="Ticker symbol e.g. 'ES', 'NQ', 'AAPL'")
    quantity: int = Field(..., gt=0, description="Number of contracts or shares")
    order_type: Literal["MKT", "LMT"] = Field("MKT", description="Order type")
    limit_price: float | None = Field(None, description="Required if order_type is LMT")
    comment: str | None = Field(None, description="Optional label for trade log")


class OrderResponse(BaseModel):
    status: Literal["accepted", "rejected"]
    order_id: str | None = None
    reason: str | None = None


@router.post("/buy", response_model=OrderResponse, summary="Place a buy order")
async def buy(order: OrderRequest, request: Request):
    risk = request.app.state.risk_manager
    monitor = request.app.state.trade_monitor

    rejection = await risk.check(order, side="BUY")
    if rejection:
        logger.warning(f"BUY rejected [{order.strategy_id}] {order.symbol}: {rejection}")
        return OrderResponse(status="rejected", reason=rejection)

    order_id = await monitor.place_order(
        symbol=order.symbol,
        side="BUY",
        quantity=order.quantity,
        order_type=order.order_type,
        limit_price=order.limit_price,
    )

    risk.record_fill(order, side="BUY")
    logger.info(f"BUY accepted [{order.strategy_id}] {order.symbol} x{order.quantity} → order_id={order_id}")
    return OrderResponse(status="accepted", order_id=order_id)


@router.post("/sell", response_model=OrderResponse, summary="Place a sell order")
async def sell(order: OrderRequest, request: Request):
    risk = request.app.state.risk_manager
    monitor = request.app.state.trade_monitor

    rejection = await risk.check(order, side="SELL")
    if rejection:
        logger.warning(f"SELL rejected [{order.strategy_id}] {order.symbol}: {rejection}")
        return OrderResponse(status="rejected", reason=rejection)

    order_id = await monitor.place_order(
        symbol=order.symbol,
        side="SELL",
        quantity=order.quantity,
        order_type=order.order_type,
        limit_price=order.limit_price,
    )

    risk.record_fill(order, side="SELL")
    logger.info(f"SELL accepted [{order.strategy_id}] {order.symbol} x{order.quantity} → order_id={order_id}")
    return OrderResponse(status="accepted", order_id=order_id)
