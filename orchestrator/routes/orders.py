"""
Orders route - relays BUY/SELL requests to the Signal Lambda endpoint so that
ALL trades flow through the full Step Functions pipeline (DynamoDB logging,
heartbeats, dead-man, portfolio risk, etc.).

Previously this route called IB Gateway directly via ib_insync, bypassing every
Lambda safety mechanism. Now it acts as a pre-check + relay:
  1. Local RiskManager runs its fast in-process checks (cooldown, position size).
  2. If approved, the request is forwarded to the Signal Lambda via API Gateway.
  3. Step Functions owns the full trade lifecycle from that point.

SIGNAL_LAMBDA_URL must be set to your API Gateway invoke URL, e.g.:
  https://xxxx.execute-api.us-east-2.amazonaws.com/prod/signal
SIGNAL_API_KEY must match the secret stored in Secrets Manager for the api_authorizer.
"""
import os
import logging
import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from typing import Literal

logger = logging.getLogger(__name__)
router = APIRouter()

SIGNAL_LAMBDA_URL = os.getenv("SIGNAL_LAMBDA_URL", "")
SIGNAL_API_KEY    = os.getenv("SIGNAL_API_KEY",    "")

if not SIGNAL_LAMBDA_URL:
    logger.warning("SIGNAL_LAMBDA_URL is not set — order routing will fail at runtime")


class OrderRequest(BaseModel):
    strategy_id: str   = Field(..., description="Unique identifier for the calling strategy")
    symbol:      str   = Field(..., description="Ticker symbol e.g. 'MES'")
    quantity:    int   = Field(..., gt=0, description="Number of contracts")
    order_type:  Literal["MKT", "LMT"] = Field("MKT")
    limit_price: float | None = Field(None, description="Required for LMT orders")
    comment:     str   | None = Field(None)


class OrderResponse(BaseModel):
    status:    Literal["accepted", "rejected"]
    trade_id:  str | None = None
    order_id:  str | None = None
    reason:    str | None = None


async def _route_to_signal_lambda(order: OrderRequest, side: str) -> OrderResponse:
    """Forward the validated order to the Signal Lambda via API Gateway."""
    if not SIGNAL_LAMBDA_URL or not SIGNAL_API_KEY:
        raise HTTPException(status_code=503, detail="Signal Lambda endpoint not configured")

    payload = {
        "strategy_id": order.strategy_id,
        "symbol":      order.symbol.upper(),
        "side":        side,
        "quantity":    order.quantity,
        "order_type":  order.order_type,
        "comment":     order.comment,
    }
    if order.order_type == "LMT" and order.limit_price is not None:
        payload["limit_price"] = order.limit_price

    try:
        resp = httpx.post(
            SIGNAL_LAMBDA_URL,
            headers={"X-API-Key": SIGNAL_API_KEY, "Content-Type": "application/json"},
            json=payload,
            timeout=10,
        )
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Signal Lambda timeout")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Signal Lambda unreachable: {e}")

    data = resp.json()

    if resp.status_code == 202:
        logger.info(f"{side} accepted [{order.strategy_id}] {order.symbol} x{order.quantity} → {data.get('trade_id')}")
        return OrderResponse(status="accepted", trade_id=data.get("trade_id"), order_id=data.get("trade_id"))

    if resp.status_code == 200 and data.get("status") == "paused":
        return OrderResponse(status="rejected", reason="Trading is currently paused")

    reason = data.get("error") or data.get("message") or f"HTTP {resp.status_code}"
    logger.warning(f"{side} rejected [{order.strategy_id}] {order.symbol}: {reason}")
    return OrderResponse(status="rejected", reason=reason)


@router.post("/buy", response_model=OrderResponse, summary="Place a buy order")
async def buy(order: OrderRequest, request: Request):
    risk = request.app.state.risk_manager

    rejection = await risk.check(order, side="BUY")
    if rejection:
        logger.warning(f"BUY pre-check rejected [{order.strategy_id}] {order.symbol}: {rejection}")
        return OrderResponse(status="rejected", reason=rejection)

    result = await _route_to_signal_lambda(order, "BUY")
    if result.status == "accepted":
        risk.record_fill(order, side="BUY")
    return result


@router.post("/sell", response_model=OrderResponse, summary="Place a sell order")
async def sell(order: OrderRequest, request: Request):
    risk = request.app.state.risk_manager

    rejection = await risk.check(order, side="SELL")
    if rejection:
        logger.warning(f"SELL pre-check rejected [{order.strategy_id}] {order.symbol}: {rejection}")
        return OrderResponse(status="rejected", reason=rejection)

    result = await _route_to_signal_lambda(order, "SELL")
    if result.status == "accepted":
        risk.record_fill(order, side="SELL")
    return result
