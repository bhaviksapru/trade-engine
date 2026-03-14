"""
Check Price Lambda - called by Express Workflow every 5s.
Fetches price from CP Gateway, evaluates stop/TP/timeout conditions.
Publishes price event to EventBridge for dashboard WebSocket.
"""
import json
import os
import boto3
import httpx
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger()
logger.setLevel(logging.INFO)

events = boto3.client("events")
dynamodb = boto3.resource("dynamodb")
trades_table = dynamodb.Table(os.environ["TRADES_TABLE"])

CP_GATEWAY_URL = os.environ["CP_GATEWAY_URL"]   # e.g. http://10.0.1.5:5000
EVENT_BUS_NAME = os.environ["EVENT_BUS_NAME"]
MAX_TRADE_DURATION_MINUTES = int(os.environ.get("MAX_TRADE_DURATION_MINUTES", "120"))
CHAIN_AT_SECONDS = 270  # chain before Express 5min limit


def handler(event, context):
    trade_id = event["trade_id"]
    symbol = event["symbol"]
    stop_loss = float(event["stop_loss_price"])
    take_profit = float(event["take_profit_price"])
    fill_price = float(event["fill_price"])
    side = event["side"]
    chain_count = int(event.get("chain_count", 0))

    # Get current price from CP Gateway
    try:
        conid = get_conid(symbol)
        price_data = get_snapshot(conid)
        current_price = float(price_data.get("31", price_data.get("84", 0)))  # last trade or bid
    except Exception as e:
        logger.error(f"Price fetch failed for {symbol}: {e}")
        # Don't exit on price fetch failure - just skip this cycle
        return {
            "stop_hit": False, "tp_hit": False, "timeout": False,
            "max_loss_hit": False, "should_chain": False,
            "current_price": 0, "error": str(e)
        }

    # Calculate unrealized P&L
    if side == "BUY":
        unrealized_pnl = (current_price - fill_price) * event.get("quantity", 1)
        stop_hit = current_price <= stop_loss
        tp_hit = current_price >= take_profit
    else:
        unrealized_pnl = (fill_price - current_price) * event.get("quantity", 1)
        stop_hit = current_price >= stop_loss
        tp_hit = current_price <= take_profit

    # Update heartbeat in DynamoDB
    now = datetime.now(timezone.utc)
    try:
        trades_table.update_item(
            Key={"trade_id": trade_id},
            UpdateExpression="SET last_heartbeat = :hb, unrealized_pnl = :pnl",
            ExpressionAttributeValues={":hb": now.isoformat(), ":pnl": str(unrealized_pnl)},
        )
    except Exception as e:
        logger.warning(f"Heartbeat update failed: {e}")

    # Publish to EventBridge for dashboard
    try:
        events.put_events(Entries=[{
            "Source": "trade-engine.check-price",
            "DetailType": "PriceUpdate",
            "EventBusName": EVENT_BUS_NAME,
            "Detail": json.dumps({
                "trade_id": trade_id,
                "symbol": symbol,
                "current_price": current_price,
                "unrealized_pnl": unrealized_pnl,
                "stop_loss_price": stop_loss,
                "take_profit_price": take_profit,
                "timestamp": now.isoformat(),
            }),
        }])
    except Exception as e:
        logger.warning(f"EventBridge publish failed: {e}")

    # Check timeout
    trade_start = datetime.fromisoformat(event.get("fill_time", now.isoformat()))
    elapsed_minutes = (now - trade_start.replace(tzinfo=timezone.utc)).total_seconds() / 60
    timeout = elapsed_minutes >= MAX_TRADE_DURATION_MINUTES

    # Check if we should chain (approaching 5min Express limit)
    # Lambda context.get_remaining_time_in_millis isn't available here but
    # we track via chain_count * 5min + elapsed time in current execution
    elapsed_in_execution = (now - datetime.fromisoformat(
        event.get("execution_start", now.isoformat())
    ).replace(tzinfo=timezone.utc)).total_seconds()
    should_chain = elapsed_in_execution >= CHAIN_AT_SECONDS

    result = {
        "stop_hit": stop_hit,
        "tp_hit": tp_hit,
        "timeout": timeout,
        "max_loss_hit": False,  # portfolio_risk Lambda handles cross-trade limits
        "should_chain": should_chain and not stop_hit and not tp_hit and not timeout,
        "current_price": current_price,
        "unrealized_pnl": unrealized_pnl,
    }

    logger.info(f"[{trade_id}] {symbol} @ {current_price} | PnL: {unrealized_pnl:.2f} | {result}")
    return result


def get_conid(symbol: str) -> str:
    """Look up IB contract ID for symbol."""
    resp = httpx.get(
        f"{CP_GATEWAY_URL}/v1/api/iserver/secdef/search",
        params={"symbol": symbol, "secType": "STK"},
        verify=False, timeout=5
    )
    resp.raise_for_status()
    data = resp.json()
    if not data:
        raise ValueError(f"No contract found for {symbol}")
    return str(data[0]["conid"])


def get_snapshot(conid: str) -> dict:
    """Fetch market data snapshot from CP Gateway."""
    resp = httpx.get(
        f"{CP_GATEWAY_URL}/v1/api/iserver/marketdata/snapshot",
        params={"conids": conid, "fields": "31,84,86"},  # last, bid, ask
        verify=False, timeout=5
    )
    resp.raise_for_status()
    data = resp.json()
    return data[0] if data else {}
