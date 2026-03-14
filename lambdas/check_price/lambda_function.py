"""
Check Price Lambda - called by Express Workflow every 5s.
Fetches MES price from CP Gateway, evaluates stop/TP/timeout conditions.
Publishes price event to EventBridge for dashboard WebSocket.

Fixes applied:
  - secType changed from STK to FUT; front-month MES contract is resolved correctly.
  - execution_start timezone handling: explicitly strips trailing 'Z' before fromisoformat()
    so it works on all Python 3.x runtimes (fromisoformat doesn't accept 'Z' pre-3.11).
  - should_chain now uses proper timezone-aware subtraction with no double-apply risk.
"""
import json
import os
import boto3
import httpx
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger()
logger.setLevel(logging.INFO)

events       = boto3.client("events")
dynamodb     = boto3.resource("dynamodb")
trades_table = dynamodb.Table(os.environ["TRADES_TABLE"])

CP_GATEWAY_URL             = os.environ["CP_GATEWAY_URL"]
EVENT_BUS_NAME             = os.environ["EVENT_BUS_NAME"]
MAX_TRADE_DURATION_MINUTES = int(os.environ.get("MAX_TRADE_DURATION_MINUTES", "120"))
CHAIN_AT_SECONDS           = 270   # chain before Express 5-min limit (300s)


def _parse_aws_dt(ts: str) -> datetime:
    """Parse an ISO-8601 timestamp from AWS (may have Z suffix or +00:00).
    Returns a timezone-aware datetime in UTC.
    Python < 3.11 fromisoformat() does not accept the 'Z' suffix.
    """
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def handler(event, context):
    trade_id    = event["trade_id"]
    symbol      = event["symbol"]
    stop_loss   = float(event["stop_loss_price"])
    take_profit = float(event["take_profit_price"])
    fill_price  = float(event["fill_price"])
    side        = event["side"]
    chain_count = int(event.get("chain_count", 0))

    # Fetch current MES price from CP Gateway
    try:
        conid      = get_conid(symbol)
        price_data = get_snapshot(conid)
        # Field 31 = last trade price; 84 = bid (fallback if no last trade yet)
        current_price = float(price_data.get("31") or price_data.get("84") or 0)
        if current_price == 0:
            raise ValueError("Zero price returned — market may be closed or snapshot not ready")
    except Exception as e:
        logger.error(f"Price fetch failed for {symbol}: {e}")
        return {
            "stop_hit": False, "tp_hit": False, "timeout": False,
            "max_loss_hit": False, "should_chain": False,
            "current_price": 0, "error": str(e)
        }

    now = datetime.now(timezone.utc)

    # P&L and stop/TP evaluation
    if side == "BUY":
        unrealized_pnl = (current_price - fill_price) * event.get("quantity", 1)
        stop_hit       = current_price <= stop_loss
        tp_hit         = current_price >= take_profit
    else:
        unrealized_pnl = (fill_price - current_price) * event.get("quantity", 1)
        stop_hit       = current_price >= stop_loss
        tp_hit         = current_price <= take_profit

    # Heartbeat
    try:
        trades_table.update_item(
            Key={"trade_id": trade_id},
            UpdateExpression="SET last_heartbeat = :hb, unrealized_pnl = :pnl",
            ExpressionAttributeValues={":hb": now.isoformat(), ":pnl": str(unrealized_pnl)},
        )
    except Exception as e:
        logger.warning(f"Heartbeat update failed: {e}")

    # Publish to EventBridge for dashboard live feed
    try:
        events.put_events(Entries=[{
            "Source":      "trade-engine.check-price",
            "DetailType":  "PriceUpdate",
            "EventBusName": EVENT_BUS_NAME,
            "Detail":      json.dumps({
                "trade_id":         trade_id,
                "symbol":           symbol,
                "current_price":    current_price,
                "unrealized_pnl":   unrealized_pnl,
                "stop_loss_price":  stop_loss,
                "take_profit_price": take_profit,
                "timestamp":        now.isoformat(),
            }),
        }])
    except Exception as e:
        logger.warning(f"EventBridge publish failed: {e}")

    # Absolute trade timeout check (uses fill_time, unaffected by chain boundaries)
    fill_time_str  = event.get("fill_time", now.isoformat())
    trade_start    = _parse_aws_dt(fill_time_str)
    elapsed_trade  = (now - trade_start).total_seconds() / 60
    timeout        = elapsed_trade >= MAX_TRADE_DURATION_MINUTES

    # Chain detection: check how long THIS Express execution has been running.
    # $$.Execution.StartTime is passed in as execution_start from the ASL.
    # FIX: use _parse_aws_dt() so the 'Z' suffix doesn't break fromisoformat().
    exec_start_str      = event.get("execution_start", now.isoformat())
    exec_start          = _parse_aws_dt(exec_start_str)
    elapsed_in_exec     = (now - exec_start).total_seconds()
    should_chain        = elapsed_in_exec >= CHAIN_AT_SECONDS

    result = {
        "stop_hit":      stop_hit,
        "tp_hit":        tp_hit,
        "timeout":       timeout,
        "max_loss_hit":  False,   # portfolio_risk Lambda handles cross-trade limits
        "should_chain":  should_chain and not stop_hit and not tp_hit and not timeout,
        "current_price": current_price,
        "unrealized_pnl": unrealized_pnl,
    }

    logger.info(f"[{trade_id}] {symbol} @ {current_price} | PnL: {unrealized_pnl:.2f} | {result}")
    return result


def get_conid(symbol: str) -> str:
    """Resolve front-month MES futures conid from IB CP Gateway.
    FIX: secType=FUT (was STK) — MES is a CME futures contract, not a stock.
    IB returns contracts with the nearest expiry first; index 0 = front-month.
    """
    resp = httpx.get(
        f"{CP_GATEWAY_URL}/v1/api/iserver/secdef/search",
        params={"symbol": symbol, "secType": "FUT"},
        verify=False, timeout=5
    )
    resp.raise_for_status()
    contracts = resp.json()
    if not contracts:
        raise ValueError(f"No futures contract found for {symbol}")
    # Front-month is always index 0 (IB sorts nearest expiry first)
    return str(contracts[0]["conid"])


def get_snapshot(conid: str) -> dict:
    """Fetch market data snapshot from CP Gateway."""
    resp = httpx.get(
        f"{CP_GATEWAY_URL}/v1/api/iserver/marketdata/snapshot",
        params={"conids": conid, "fields": "31,84,86"},   # last, bid, ask
        verify=False, timeout=5
    )
    resp.raise_for_status()
    data = resp.json()
    return data[0] if data else {}
