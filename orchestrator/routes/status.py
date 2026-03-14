"""
Status route - health check for the orchestrator and Signal Lambda reachability.
ib_insync connection check removed since the orchestrator no longer connects directly.
"""
import os
import httpx
import logging
from fastapi import APIRouter, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter()

SIGNAL_LAMBDA_URL = os.getenv("SIGNAL_LAMBDA_URL", "")


class HealthResponse(BaseModel):
    orchestrator:   str
    signal_lambda:  str
    ready:          bool


@router.get("/health", response_model=HealthResponse, summary="Health check")
async def health(request: Request):
    """Check that the orchestrator is up and the Signal Lambda endpoint is reachable."""
    lambda_status = "unconfigured"
    if SIGNAL_LAMBDA_URL:
        try:
            # We can't call the signal endpoint without a full payload + auth,
            # so just verify the API Gateway host is reachable via OPTIONS/HEAD.
            r = httpx.head(SIGNAL_LAMBDA_URL, timeout=3)
            # API Gateway returns 403 (no auth) or 200 — either means it's reachable.
            lambda_status = "reachable" if r.status_code in (200, 403, 405) else f"http_{r.status_code}"
        except Exception as e:
            lambda_status = f"unreachable: {e}"

    ready = lambda_status in ("reachable",)
    return HealthResponse(
        orchestrator=  "ok",
        signal_lambda= lambda_status,
        ready=         ready,
    )


@router.get("/positions", summary="Risk manager position snapshot")
async def positions(request: Request):
    """Returns the orchestrator's in-process position state (soft limit tracker)."""
    rm = request.app.state.risk_manager
    return {
        "positions": {str(k): v for k, v in rm._positions.items()},
        "daily_loss": dict(rm._daily_loss),
    }
