"""
WebSocket live route — streams real-time trade events to the browser dashboard.
Subscribes to EventBridge events published by check_price Lambda.
Uses asyncio queue to fan out to connected clients.
"""
import os
import json
import asyncio
import logging
import boto3
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from auth.cognito import get_jwks_client
import jwt

logger = logging.getLogger(__name__)
router = APIRouter()

# Connected clients: set of (websocket, queue) pairs
_clients: set = set()
_clients_lock = asyncio.Lock()

COGNITO_CLIENT_ID = os.environ["COGNITO_CLIENT_ID"]
ALLOWED_EMAIL     = os.environ["ALLOWED_GOOGLE_EMAIL"]


async def broadcast(message: dict):
    """Called by EventBridge poller to push to all connected clients."""
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
        client = get_jwks_client()
        signing_key = client.get_signing_key_from_jwt(token)
        payload = jwt.decode(token, signing_key.key, algorithms=["RS256"],
                             audience=COGNITO_CLIENT_ID, options={"verify_exp": True})
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
        # Send initial state snapshot on connect
        await websocket.send_json({"type": "connected", "message": "Live stream active"})

        while True:
            # Wait for event from broadcast() with a 30s ping to keep connection alive
            try:
                message = await asyncio.wait_for(queue.get(), timeout=30)
                await websocket.send_json(message)
            except asyncio.TimeoutError:
                # Ping to keep connection alive
                await websocket.send_json({"type": "ping"})

    except WebSocketDisconnect:
        logger.info(f"WebSocket client disconnected: {email}")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        async with _clients_lock:
            _clients.discard((websocket, queue))


# ── EventBridge SQS poller ───────────────────────────────────────────────────
# EventBridge → SQS queue → this background task → WebSocket clients

SQS_QUEUE_URL = os.environ.get("EVENTS_SQS_QUEUE_URL", "")
sqs = boto3.client("sqs") if SQS_QUEUE_URL else None


async def poll_eventbridge_sqs():
    """
    Background task that polls SQS (fed by EventBridge) and broadcasts
    price/trade events to all connected WebSocket clients.
    Runs for the lifetime of the Fargate task.
    """
    if not sqs or not SQS_QUEUE_URL:
        logger.warning("No SQS queue configured — WebSocket live feed disabled")
        return

    logger.info("Starting EventBridge SQS poller...")
    while True:
        try:
            response = sqs.receive_message(
                QueueUrl=SQS_QUEUE_URL,
                MaxNumberOfMessages=10,
                WaitTimeSeconds=5,      # long polling
            )
            messages = response.get("Messages", [])

            for msg in messages:
                try:
                    body = json.loads(msg["Body"])
                    detail = body.get("detail", {})
                    event_type = body.get("detail-type", "unknown")

                    await broadcast({
                        "type": event_type,
                        "data": detail,
                    })

                    # Delete from SQS after processing
                    sqs.delete_message(
                        QueueUrl=SQS_QUEUE_URL,
                        ReceiptHandle=msg["ReceiptHandle"]
                    )
                except Exception as e:
                    logger.error(f"Failed to process SQS message: {e}")

        except Exception as e:
            logger.error(f"SQS poll error: {e}")
            await asyncio.sleep(5)
