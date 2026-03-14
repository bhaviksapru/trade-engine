"""
WebSocket live route - streams real-time trade events to the browser dashboard.
Subscribes to EventBridge events published by check_price Lambda via an SQS queue.
Uses asyncio queue to fan out to connected clients.

Fix: blocking boto3 SQS calls (receive_message, delete_message) are now run inside
asyncio.get_event_loop().run_in_executor(None, ...) so they never block the event
loop thread. Previously they blocked for up to WaitTimeSeconds=5 per poll cycle,
stalling WebSocket sends, pings, and all concurrent HTTP requests during that window.
"""
import os
import json
import asyncio
import logging
import boto3
from functools import partial
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from auth.cognito import get_jwks_client
import jwt

logger = logging.getLogger(__name__)
router = APIRouter()

# Connected clients: set of (websocket, queue) pairs
_clients: set         = set()
_clients_lock         = asyncio.Lock()

COGNITO_CLIENT_ID = os.environ["COGNITO_CLIENT_ID"]
ALLOWED_EMAIL     = os.environ["ALLOWED_GOOGLE_EMAIL"]


async def broadcast(message: dict):
    """Called by the SQS poller to push an event to all connected clients."""
    async with _clients_lock:
        dead = set()
        for ws, queue in _clients:
            try:
                await queue.put(message)
            except Exception:
                dead.add((ws, queue))
        _clients.difference_update(dead)


@router.websocket("")
async def websocket_live(websocket: WebSocket, token: str = Query(...)):
    """
    WSS /live?token=<cognito_id_token>
    Browser connects with the Cognito JWT as a query param
    (Authorization header not available in browser WebSocket API).
    """
    # Verify JWT before accepting connection
    try:
        client      = get_jwks_client()
        signing_key = client.get_signing_key_from_jwt(token)
        payload     = jwt.decode(
            token, signing_key.key, algorithms=["RS256"],
            audience=COGNITO_CLIENT_ID, options={"verify_exp": True}
        )
        email = payload.get("email", payload.get("cognito:username", ""))
        if email != ALLOWED_EMAIL:
            await websocket.close(code=4003, reason="Access denied")
            return
    except Exception as e:
        logger.warning(f"WebSocket auth failed: {e}")
        await websocket.close(code=4001, reason="Unauthorized")
        return

    await websocket.accept()
    queue: asyncio.Queue = asyncio.Queue(maxsize=100)

    async with _clients_lock:
        _clients.add((websocket, queue))

    logger.info(f"WebSocket client connected: {email}")

    try:
        await websocket.send_json({"type": "connected", "message": "Live stream active"})

        while True:
            # Wait for an event from broadcast() with a 30s ping timeout
            try:
                message = await asyncio.wait_for(queue.get(), timeout=30)
                await websocket.send_json(message)
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "ping"})

    except WebSocketDisconnect:
        logger.info(f"WebSocket client disconnected: {email}")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        async with _clients_lock:
            _clients.discard((websocket, queue))


# ---------------------------------------------------------------------------
# EventBridge → SQS poller
# ---------------------------------------------------------------------------
# EventBridge fires a PriceUpdate event for every check_price Lambda tick.
# Those events land in an SQS queue. This background task drains that queue
# and broadcasts each event to all connected WebSocket clients.
#
# FIX: All boto3 SQS calls are wrapped in run_in_executor so they run in the
# default ThreadPoolExecutor, never on the asyncio event loop thread.
# The old synchronous sqs.receive_message(WaitTimeSeconds=5) call blocked the
# entire event loop for up to 5 s per iteration, stalling WebSocket writes,
# pings, and all concurrent HTTP requests to the dashboard API.
# ---------------------------------------------------------------------------

SQS_QUEUE_URL = os.environ.get("EVENTS_SQS_QUEUE_URL", "")
_sqs          = boto3.client("sqs") if SQS_QUEUE_URL else None


def _sqs_receive() -> list:
    """Blocking SQS long-poll — called in a thread via run_in_executor."""
    if not _sqs:
        return []
    resp = _sqs.receive_message(
        QueueUrl=SQS_QUEUE_URL,
        MaxNumberOfMessages=10,
        WaitTimeSeconds=5,       # long poll; blocks the worker thread, not the event loop
    )
    return resp.get("Messages", [])


def _sqs_delete(receipt_handle: str) -> None:
    """Blocking SQS delete — called in a thread via run_in_executor."""
    if _sqs:
        _sqs.delete_message(QueueUrl=SQS_QUEUE_URL, ReceiptHandle=receipt_handle)


async def poll_eventbridge_sqs():
    """
    Background asyncio task started in dashboard-api/main.py lifespan.
    Polls SQS (fed by EventBridge) and broadcasts price/trade events to
    all connected WebSocket clients for the lifetime of the Fargate task.
    """
    if not _sqs or not SQS_QUEUE_URL:
        logger.warning("No SQS queue configured — WebSocket live feed disabled")
        return

    logger.info("Starting EventBridge SQS poller (non-blocking)...")
    loop = asyncio.get_event_loop()

    while True:
        try:
            # Run the blocking receive in a thread pool worker
            messages = await loop.run_in_executor(None, _sqs_receive)

            for msg in messages:
                try:
                    body       = json.loads(msg["Body"])
                    detail     = body.get("detail", {})
                    event_type = body.get("detail-type", "unknown")

                    await broadcast({"type": event_type, "data": detail})

                    # Delete in a thread pool worker too
                    await loop.run_in_executor(
                        None,
                        partial(_sqs_delete, msg["ReceiptHandle"])
                    )
                except Exception as e:
                    logger.error(f"Failed to process SQS message: {e}")

        except Exception as e:
            logger.error(f"SQS poll error: {e}")
            await asyncio.sleep(5)   # back-off on unexpected errors
