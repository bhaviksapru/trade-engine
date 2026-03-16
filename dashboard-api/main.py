import asyncio
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import os
import logging

from auth.cognito import verify_token
from routes.positions import router as positions_router
from routes.health import router as health_router
from routes.actions import router as actions_router
from websocket.live import router as ws_router, poll_eventbridge_sqs

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

ALLOWED_ORIGINS = os.environ.get("ALLOWED_ORIGINS", "").split(",")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Trade Engine Dashboard API starting...")
    # FIX: Start the EventBridge→SQS→WebSocket fan-out background task.
    # Without this the WebSocket live feed only ever emits keep-alive pings
    # because poll_eventbridge_sqs() was defined but never scheduled.
    asyncio.create_task(poll_eventbridge_sqs())
    logger.info("EventBridge SQS poller started")
    yield
    logger.info("Shutting down.")


app = FastAPI(
    title="Trade Engine Dashboard API",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None,    # disable public Swagger
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(positions_router, prefix="/positions", tags=["Positions"], dependencies=[Depends(verify_token)])
app.include_router(health_router,    prefix="/health",    tags=["Health"],    dependencies=[Depends(verify_token)])
app.include_router(actions_router,   prefix="/actions",   tags=["Actions"],   dependencies=[Depends(verify_token)])
app.include_router(ws_router,        prefix="/live",      tags=["WebSocket"])  # auth inside WS handler
