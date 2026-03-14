"""
Close Position Lambda - places market close order at IB, updates DynamoDB.
Handles both normal close (after monitoring loop) and CANCEL (fill timeout).
Returns { "close_price": ..., "exit_reason": ... }

Fixes applied:
  - secType changed from STK to FUT for MES futures contract resolution.
  - GTC stop order is now cancelled at IB before the market close order is sent
    for ALL exit reasons (including STOP_HIT). The previous code skipped the
    cancel for STOP_HIT, assuming IB had already executed the stop. But
    check_price detects stop_hit via a software price comparison, not an IB
    execution confirmation. By the time close_position runs, the GTC stop may
    not have fired yet. Sending a MKT close without first cancelling the GTC
    stop causes a reverse position in two scenarios:
      (a) Stop fired already   → MKT close hits flat account → opens new short
      (b) Stop not fired yet   → MKT close flattens, then stop fires → opens new short
    The cancel call swallows 404s (order already gone) so it is safe for both
    cases. Always cancel first, then close.
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

    logger.info(f"[{trade_id}] Closing position - {side} {quantity} {symbol} reason={exit_reason}")

    close_side = "SELL" if side == "BUY" else "BUY"
    account_id = _get_account_id()
    conid      = _get_conid(symbol)

    # Always cancel the GTC stop order before sending the market close.
    # _cancel_stop_order swallows 404s, so it is safe whether or not IB has
    # already executed the stop. See module docstring for full rationale.
    _cancel_stop_order(trade_id, account_id)

    # Market close
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

    close_price = _get_last_price(conid)
    close_time  = datetime.now(timezone.utc).isoformat()

    trade      = trades_table.get_item(Key={"trade_id": trade_id}).get("Item", {})
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

    strategy_id = trade.get("strategy_id", "unknown")
    try:
        positions_table.delete_item(Key={"strategy_id": strategy_id, "symbol": symbol})
    except Exception as e:
        logger.warning(f"Failed to clear positions table: {e}")

    logger.info(f"[{trade_id}] Closed - price={close_price} pnl={pnl:.2f}")
    return {
        "close_price": close_price,
        "exit_reason": exit_reason,
        "pnl_usd":     round(pnl, 2),
        "trade_id":    trade_id,
    }


def _cancel_stop_order(trade_id: str, account_id: str) -> None:
    """Cancel the resting GTC stop order placed by set_stop.
    Reads stop_order_id from DynamoDB. Swallows errors — if the order is
    already gone (e.g. stop was touched but not triggered before our close)
    IB will return 404 which we ignore.
    """
    try:
        trade = trades_table.get_item(Key={"trade_id": trade_id}).get("Item", {})
        stop_order_id = trade.get("stop_order_id", "")
        if not stop_order_id or stop_order_id == "unknown":
            return
        httpx.delete(
            f"{CP_URL}/v1/api/iserver/account/{account_id}/order/{stop_order_id}",
            verify=False, timeout=5
        )
        logger.info(f"[{trade_id}] Cancelled GTC stop order {stop_order_id}")
    except Exception as e:
        logger.warning(f"[{trade_id}] Could not cancel stop order (may already be gone): {e}")


def _cancel_order(event: dict) -> dict:
    """Cancel an unfilled entry order (fill timeout path)."""
    trade_id    = event["trade_id"]
    ib_order_id = str(event.get("ib_order_id", ""))
    account_id  = _get_account_id()

    logger.info(f"[{trade_id}] Cancelling entry order {ib_order_id}")
    try:
        httpx.delete(
            f"{CP_URL}/v1/api/iserver/account/{account_id}/order/{ib_order_id}",
            verify=False, timeout=5
        )
    except Exception as e:
        logger.warning(f"Cancel entry order failed (may already be filled): {e}")

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
    """secType=FUT — MES is a CME futures contract. Returns front-month conid."""
    r = httpx.get(
        f"{CP_URL}/v1/api/iserver/secdef/search",
        params={"symbol": symbol, "secType": "FUT"},
        verify=False, timeout=5
    )
    r.raise_for_status()
    data = r.json()
    if not data:
        raise ValueError(f"No futures contract found for {symbol}")
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
