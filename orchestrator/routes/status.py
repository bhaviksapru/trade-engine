from fastapi import APIRouter, Request
from pydantic import BaseModel
from typing import Any

router = APIRouter()


class HealthResponse(BaseModel):
    orchestrator: str
    ib_gateway: str
    connected: bool


@router.get("/health", response_model=HealthResponse, summary="Health check")
async def health(request: Request):
    monitor = request.app.state.trade_monitor
    connected = monitor.is_connected()
    return HealthResponse(
        orchestrator="ok",
        ib_gateway="ok" if connected else "unreachable",
        connected=connected,
    )


@router.get("/positions", summary="All open positions")
async def positions(request: Request) -> list[dict[str, Any]]:
    monitor = request.app.state.trade_monitor
    return await monitor.get_positions()
