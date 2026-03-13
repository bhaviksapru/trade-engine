"""
Close Position Lambda — places market close order at IB, updates DynamoDB.
Handles both normal close (after monitoring loop) and CANCEL (fill timeout).
Returns { "close_price": ..., "exit_reason": ... }
"""
import os, boto3, httpx, logging
from datetime import datetime, timezone

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb        = boto3.resource("dynamodb")
trades_table    = dynamodb.Table(os.environ["TRADES_TABLE"])
positions_table = dynamodb.Table(os.environ["POSITIONS_TABLE"])
CP_URL          = os.environ["CP_GATEWAY_URL"]


def handler(event, context):
    trade_id    = event["trade_id"]
    action      = event.get("action", "CLOSE")
    exit_reason = event.get("exit_reason", "MANUAL")

    if action == "CANCEL":
        return _cancel_order(event)

    symbol   = event["symbol"]
    side     = event["side"]
    quantity = int(event["quantity"])

    logger.info(f"[{trade_id}] Closing position — {side} {quantity} {symbol} reason={exit_reason}")

    close_side = "SELL" if side == "BUY" else "BUY"
    account_id = _get_account_id()
    conid      = _get_conid(symbol)

    resp = httpx.post(
        f"{CP_URL}/v1/api/iserver/account/{account_id}/orders",
        json={"orders": [{
            "conid":     int(conid),
            "orderType": "MKT",
            "side":      close_side,
            "quantity":  quantity,
            "tif":       "DAY",
        }]},
        verify=False, timeout=10
    )
    resp.raise_for_status()

    # Get approximate close price from market data
    close_price = _get_last_price(conid)
    close_time  = datetime.now(timezone.utc).isoformat()

    # Get fill price to calculate P&L
    trade = trades_table.get_item(Key={"trade_id": trade_id}).get("Item", {})
    fill_price = float(trade.get("fill_price", 0))

    if side == "BUY":
        pnl = (close_price - fill_price) * quantity
    else:
        pnl = (fill_price - close_price) * quantity

    trades_table.update_item(
        Key={"trade_id": trade_id},
        UpdateExpression=(
            "SET close_price = :cp, close_time = :ct, exit_reason = :er, "
            "#s = :status, pnl_usd = :pnl"
        ),
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={
            ":cp":     str(close_price),
            ":ct":     close_time,
            ":er":     exit_reason,
            ":status": "CLOSED",
            ":pnl":    str(round(pnl, 2)),
        },
    )

    # Clear from positions table
    strategy_id = trade.get("strategy_id", "unknown")
    try:
        positions_table.delete_item(Key={"strategy_id": strategy_id, "symbol": symbol})
    except Exception as e:
        logger.warning(f"Failed to clear positions table: {e}")

    logger.info(f"[{trade_id}] Closed — price={close_price} pnl={pnl:.2f}")
    return {
        "close_price":  close_price,
        "exit_reason":  exit_reason,
        "pnl_usd":      round(pnl, 2),
        "trade_id":     trade_id,
    }


def _cancel_order(event: dict) -> dict:
    trade_id    = event["trade_id"]
    ib_order_id = str(event.get("ib_order_id", ""))
    account_id  = _get_account_id()

    logger.info(f"[{trade_id}] Cancelling order {ib_order_id}")
    try:
        httpx.delete(
            f"{CP_URL}/v1/api/iserver/account/{account_id}/order/{ib_order_id}",
            verify=False, timeout=5
        )
    except Exception as e:
        logger.warning(f"Cancel order failed (may already be filled): {e}")

    trades_table.update_item(
        Key={"trade_id": trade_id},
        UpdateExpression="SET #s = :s, exit_reason = :r, close_time = :t",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={
            ":s": "CANCELLED", ":r": "FILL_TIMEOUT",
            ":t": datetime.now(timezone.utc).isoformat(),
        },
    )
    return {"close_price": 0, "exit_reason": "FILL_TIMEOUT", "trade_id": trade_id}


def _get_account_id() -> str:
    r = httpx.get(f"{CP_URL}/v1/api/portfolio/accounts", verify=False, timeout=5)
    r.raise_for_status()
    return r.json()[0]["accountId"]


def _get_conid(symbol: str) -> str:
    r = httpx.get(f"{CP_URL}/v1/api/iserver/secdef/search",
                  params={"symbol": symbol, "secType": "STK"}, verify=False, timeout=5)
    r.raise_for_status()
    data = r.json()
    if not data:
        raise ValueError(f"No contract found for {symbol}")
    return str(data[0]["conid"])


def _get_last_price(conid: str) -> float:
    try:
        r = httpx.get(
            f"{CP_URL}/v1/api/iserver/marketdata/snapshot",
            params={"conids": conid, "fields": "31"},
            verify=False, timeout=5
        )
        r.raise_for_status()
        data = r.json()
        return float(data[0].get("31", 0)) if data else 0.0
    except Exception:
        return 0.0
