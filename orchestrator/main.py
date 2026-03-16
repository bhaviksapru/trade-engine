"""
Trade Orchestrator - pre-check + relay layer between NinjaTrader and the Signal Lambda.

All trades now flow through the Signal Lambda → Step Functions pipeline.
The orchestrator's job is limited to:
  1. API key authentication
  2. Fast in-process risk pre-checks (cooldown, soft position limit)
  3. Forwarding approved orders to the Signal Lambda

The previous ib_insync direct-to-gateway path is removed. It bypassed DynamoDB,
heartbeats, the dead-man Lambda, and portfolio-risk monitoring entirely.
"""
from fastapi import FastAPI, Depends, HTTPException, Security
from fastapi.security.api_key import APIKeyHeader
from contextlib import asynccontextmanager
import os
import logging

from routes.orders import router as orders_router
from routes.status import router as status_router
from risk.risk_manager import RiskManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

API_KEY        = os.getenv("ORCHESTRATOR_API_KEY", "change-me")
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=True)


async def verify_api_key(key: str = Security(api_key_header)):
    if key != API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API key")
    return key


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Trade Orchestrator...")
    app.state.risk_manager = RiskManager()
    # TradeMonitor (ib_insync) removed — orders are relayed to the Signal Lambda.
    # The Signal Lambda owns the full lifecycle via Step Functions.
    yield
    logger.info("Shutting down Trade Orchestrator.")


app = FastAPI(
    title="Trade Orchestrator",
    description="Pre-check and relay layer — forwards approved signals to the Signal Lambda.",
    version="1.1.0",
    lifespan=lifespan,
)

app.include_router(orders_router, prefix="/orders", tags=["Orders"], dependencies=[Depends(verify_api_key)])
app.include_router(status_router, prefix="",        tags=["Status"], dependencies=[Depends(verify_api_key)])
