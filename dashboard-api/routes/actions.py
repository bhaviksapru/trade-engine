"""
Actions route — POST endpoints for dashboard controls.
All require valid Cognito JWT (enforced at app level).
"""
import os
import json
import boto3
import httpx
import logging
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)
router = APIRouter()

dynamodb   = boto3.resource("dynamodb")
sf         = boto3.client("stepfunctions")
sns        = boto3.client("sns")
config_table = dynamodb.Table(os.environ["CONFIG_TABLE"])
trades_table = dynamodb.Table(os.environ["TRADES_TABLE"])
CP_GATEWAY_URL = os.environ["CP_GATEWAY_URL"]
SNS_TOPIC_ARN  = os.environ["SNS_TOPIC_ARN"]


# ── Close All Positions ──────────────────────────────────────────────────────

@router.post("/close-all-positions")
async def close_all_positions():
    """Emergency: close every open position and stop all workflows."""
    result = trades_table.query(
        IndexName="StatusIndex",
        KeyConditionExpression="status = :s",
        ExpressionAttributeValues={":s": "OPEN"},
    )
    open_trades = result.get("Items", [])

    if not open_trades:
        return {"message": "No open positions", "closed": []}

    account_id = _get_account_id()
    closed, failed = [], []

    for trade in open_trades:
        trade_id = trade["trade_id"]
        try:
            # Stop SF execution
            if trade.get("execution_arn"):
                sf.stop_execution(executionArn=trade["execution_arn"], cause="ManualCloseAll")

            # Market close at IB
            close_side = "SELL" if trade["side"] == "BUY" else "BUY"
            conid = _get_conid(trade["symbol"])
            httpx.post(
                f"{CP_GATEWAY_URL}/v1/api/iserver/account/{account_id}/orders",
                json={"orders": [{"conid": int(conid), "orderType": "MKT",
                                  "side": close_side, "quantity": int(trade["quantity"]), "tif": "DAY"}]},
                verify=False, timeout=10
            ).raise_for_status()

            # Update DynamoDB
            trades_table.update_item(
                Key={"trade_id": trade_id},
                UpdateExpression="SET #s = :s, exit_reason = :r, close_time = :t",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={
                    ":s": "CLOSED", ":r": "MANUAL_CLOSE_ALL",
                    ":t": datetime.now(timezone.utc).isoformat(),
                }
            )
            closed.append(trade_id)
        except Exception as e:
            logger.error(f"Failed to close {trade_id}: {e}")
            failed.append({"trade_id": trade_id, "error": str(e)})

    _disable_trading("manual_close_all")
    sns.publish(TopicArn=SNS_TOPIC_ARN,
                Message=f"Manual close all: {len(closed)} closed, {len(failed)} failed",
                Subject="Trade Engine — Close All")

    return {"closed": closed, "failed": failed}


# ── Close Single Position ────────────────────────────────────────────────────

@router.post("/close-position/{trade_id}")
async def close_position(trade_id: str):
    result = trades_table.get_item(Key={"trade_id": trade_id})
    trade = result.get("Item")
    if not trade:
        raise HTTPException(404, f"Trade {trade_id} not found")
    if trade["status"] != "OPEN":
        raise HTTPException(400, f"Trade {trade_id} is not open (status: {trade['status']})")

    account_id = _get_account_id()
    close_side = "SELL" if trade["side"] == "BUY" else "BUY"
    conid = _get_conid(trade["symbol"])

    if trade.get("execution_arn"):
        sf.stop_execution(executionArn=trade["execution_arn"], cause="ManualClose")

    httpx.post(
        f"{CP_GATEWAY_URL}/v1/api/iserver/account/{account_id}/orders",
        json={"orders": [{"conid": int(conid), "orderType": "MKT",
                          "side": close_side, "quantity": int(trade["quantity"]), "tif": "DAY"}]},
        verify=False, timeout=10
    ).raise_for_status()

    trades_table.update_item(
        Key={"trade_id": trade_id},
        UpdateExpression="SET #s = :s, exit_reason = :r, close_time = :t",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":s": "CLOSED", ":r": "MANUAL_CLOSE",
                                   ":t": datetime.now(timezone.utc).isoformat()},
    )
    return {"message": f"Position {trade_id} closed"}


# ── Trading Enable/Disable ───────────────────────────────────────────────────

@router.post("/pause-trading")
async def pause_trading():
    _disable_trading("manual_pause")
    return {"trading_enabled": False}


@router.post("/resume-trading")
async def resume_trading():
    config_table.put_item(Item={"pk": "trading_enabled", "value": True, "reason": "manual_resume"})
    return {"trading_enabled": True}


# ── Notifications ────────────────────────────────────────────────────────────

class NotificationPrefs(BaseModel):
    enabled: bool
    phone: Optional[str] = None
    events: Optional[dict] = None


@router.post("/notifications/update")
async def update_notifications(prefs: NotificationPrefs):
    item = {"pk": "notification_preferences", "enabled": prefs.enabled}
    if prefs.phone:
        item["phone"] = prefs.phone
    if prefs.events:
        item["events"] = prefs.events
    config_table.put_item(Item=item)
    return {"message": "Notification preferences updated", "enabled": prefs.enabled}


# ── Risk Parameters ──────────────────────────────────────────────────────────

class RiskParams(BaseModel):
    max_daily_loss_usd: Optional[float] = None
    max_position_size:  Optional[int]   = None
    order_cooldown_secs: Optional[int]  = None


@router.post("/set-risk-parameters")
async def set_risk_parameters(params: RiskParams):
    updates = {k: v for k, v in params.dict().items() if v is not None}
    if not updates:
        raise HTTPException(400, "No parameters provided")

    expr = "SET " + ", ".join(f"{k} = :{k}" for k in updates)
    vals = {f":{k}": v for k, v in updates.items()}
    vals[":updated"] = datetime.now(timezone.utc).isoformat()
    expr += ", updated_at = :updated"

    config_table.update_item(
        Key={"pk": "risk_parameters"},
        UpdateExpression=expr,
        ExpressionAttributeValues=vals,
    )
    return {"message": "Risk parameters updated", "updated": updates}


# ── Helpers ──────────────────────────────────────────────────────────────────

def _disable_trading(reason: str):
    config_table.put_item(Item={"pk": "trading_enabled", "value": False, "reason": reason})


def _get_account_id() -> str:
    resp = httpx.get(f"{CP_GATEWAY_URL}/v1/api/portfolio/accounts", verify=False, timeout=5)
    resp.raise_for_status()
    return resp.json()[0]["accountId"]


def _get_conid(symbol: str) -> str:
    resp = httpx.get(f"{CP_GATEWAY_URL}/v1/api/iserver/secdef/search",
                     params={"symbol": symbol, "secType": "STK"}, verify=False, timeout=5)
    resp.raise_for_status()
    data = resp.json()
    if not data:
        raise HTTPException(404, f"Symbol {symbol} not found")
    return str(data[0]["conid"])
