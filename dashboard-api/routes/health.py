import os
import httpx
import logging
from fastapi import APIRouter

logger = logging.getLogger(__name__)
router = APIRouter()

CP_GATEWAY_URL = os.environ["CP_GATEWAY_URL"]


@router.get("")
async def health():
    """CP Gateway auth status + overall system health."""
    try:
        resp = httpx.get(f"{CP_GATEWAY_URL}/v1/api/iserver/auth/status",
                         verify=False, timeout=5)
        auth = resp.json()
        cp_status     = "authenticated" if auth.get("authenticated") else "unauthenticated"
        cp_connected  = auth.get("connected", False)
    except Exception as e:
        cp_status    = "unreachable"
        cp_connected = False
        logger.warning(f"CP Gateway health check failed: {e}")

    return {
        "orchestrator":  "ok",
        "cp_gateway":    cp_status,
        "ib_connected":  cp_connected,
        "healthy":       cp_status == "authenticated" and cp_connected,
    }
