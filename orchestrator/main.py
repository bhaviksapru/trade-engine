from fastapi import FastAPI, Depends, HTTPException, Security
from fastapi.security.api_key import APIKeyHeader
from contextlib import asynccontextmanager
import os
import logging

from routes.orders import router as orders_router
from routes.status import router as status_router
from monitor.trade_monitor import TradeMonitor
from risk.risk_manager import RiskManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

API_KEY = os.getenv("ORCHESTRATOR_API_KEY", "change-me")
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=True)


async def verify_api_key(key: str = Security(api_key_header)):
    if key != API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API key")
    return key


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Trade Orchestrator...")
    app.state.risk_manager = RiskManager()
    app.state.trade_monitor = TradeMonitor()
    await app.state.trade_monitor.connect()
    yield
    logger.info("Shutting down Trade Orchestrator...")
    await app.state.trade_monitor.disconnect()


app = FastAPI(
    title="Trade Orchestrator",
    description="Risk-managed order routing layer between NinjaTrader and IB Gateway.",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(orders_router, prefix="/orders", tags=["Orders"], dependencies=[Depends(verify_api_key)])
app.include_router(status_router, prefix="", tags=["Status"], dependencies=[Depends(verify_api_key)])
