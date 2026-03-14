"""
Positions and trades route - read-only DynamoDB queries for the dashboard.
"""
import os
import boto3
import logging
from fastapi import APIRouter, Query
from boto3.dynamodb.conditions import Key, Attr
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)
router = APIRouter()

dynamodb = boto3.resource("dynamodb")
trades_table   = dynamodb.Table(os.environ["TRADES_TABLE"])
config_table   = dynamodb.Table(os.environ["CONFIG_TABLE"])
positions_table = dynamodb.Table(os.environ["POSITIONS_TABLE"])


@router.get("")
async def get_positions():
    """Current open positions with unrealized P&L."""
    result = trades_table.query(
        IndexName="StatusIndex",
        KeyConditionExpression=Key("status").eq("OPEN"),
    )
    return {"positions": result.get("Items", []), "count": result.get("Count", 0)}


@router.get("/trades")
async def get_trades(
    days: int = Query(7, ge=1, le=90),
    strategy_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
):
    """Trade history with optional filters."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    if strategy_id:
        result = trades_table.query(
            IndexName="StrategyIndex",
            KeyConditionExpression=Key("strategy_id").eq(strategy_id) & Key("created_at").gte(cutoff),
        )
    else:
        result = trades_table.scan(
            FilterExpression=Attr("created_at").gte(cutoff)
        )

    trades = result.get("Items", [])
    if status:
        trades = [t for t in trades if t.get("status") == status]

    trades.sort(key=lambda t: t.get("created_at", ""), reverse=True)
    return {"trades": trades, "count": len(trades)}


@router.get("/risk")
async def get_risk():
    """Current risk state - daily P&L, limits, trading status."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    daily_pnl_item = config_table.get_item(Key={"pk": f"daily_pnl#{today}"}).get("Item", {})
    risk_params    = config_table.get_item(Key={"pk": "risk_parameters"}).get("Item", {})
    trading_status = config_table.get_item(Key={"pk": "trading_enabled"}).get("Item", {})

    open_count = trades_table.query(
        IndexName="StatusIndex",
        KeyConditionExpression=Key("status").eq("OPEN"),
        Select="COUNT",
    ).get("Count", 0)

    return {
        "trading_enabled":   trading_status.get("value", True),
        "trading_disabled_reason": trading_status.get("reason"),
        "daily_pnl":         float(daily_pnl_item.get("total_pnl", 0)),
        "daily_trade_count": int(daily_pnl_item.get("trade_count", 0)),
        "win_count":         int(daily_pnl_item.get("win_count", 0)),
        "loss_count":        int(daily_pnl_item.get("loss_count", 0)),
        "open_positions":    open_count,
        "max_daily_loss":    float(risk_params.get("max_daily_loss_usd", 500)),
        "max_position_size": int(risk_params.get("max_position_size", 10)),
    }
