"""
Wait For Fill Lambda - polls CP Gateway until order is filled or raises FillTimeout.
Step Functions retries with exponential backoff via the ASL Retry config.

Fix: removed "presubmitted" from the filled-status check. presubmitted means the
order is queued in IB's system but NOT yet sent to the exchange. Treating it as a
fill causes stops to be set and monitoring to begin on a non-existent position.
Only "filled" (and "partialfilled" with a full-fill guard) are valid fill statuses.
"""
import os, boto3, httpx, logging
from datetime import datetime, timezone

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb     = boto3.resource("dynamodb")
trades_table = dynamodb.Table(os.environ["TRADES_TABLE"])
CP_URL       = os.environ["CP_GATEWAY_URL"]


class FillPending(Exception):
    """Raised when order is not yet filled — triggers SF retry."""
    pass


class FillTimeout(Exception):
    """Raised when order is definitively not filled — triggers cancel path."""
    pass


def handler(event, context):
    trade_id    = event["trade_id"]
    ib_order_id = str(event["ib_order_id"])
    symbol      = event["symbol"]

    logger.info(f"[{trade_id}] Polling fill for order {ib_order_id}")

    try:
        r = httpx.get(
            f"{CP_URL}/v1/api/iserver/account/orders",
            params={"filters": "filled,inactive"},
            verify=False, timeout=5
        )
        r.raise_for_status()
        orders = r.json().get("orders", [])
    except Exception as e:
        logger.warning(f"[{trade_id}] Order status fetch failed: {e}")
        raise FillPending(f"CP Gateway unreachable: {e}")

    for order in orders:
        order_id = str(order.get("orderId") or order.get("id", ""))
        if order_id != ib_order_id:
            continue

        status = order.get("status", "").lower()
        logger.info(f"[{trade_id}] Order {ib_order_id} status: {status}")

        # FIX: Only treat fully-filled orders as fills.
        # "presubmitted" = order queued in IB but not yet sent to exchange — NOT a fill.
        # "partialfilled" is excluded here; partial fills are treated as pending since
        # MES is traded in whole-contract increments and we expect a full fill.
        if status == "filled":
            fill_price = float(order.get("avgPrice") or order.get("price") or 0)
            fill_time  = datetime.now(timezone.utc).isoformat()

            trades_table.update_item(
                Key={"trade_id": trade_id},
                UpdateExpression="SET fill_price = :fp, fill_time = :ft, #s = :s",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={
                    ":fp": str(fill_price), ":ft": fill_time, ":s": "FILLED"
                },
            )
            logger.info(f"[{trade_id}] FILLED at {fill_price}")
            return {"fill_price": fill_price, "fill_time": fill_time, "trade_id": trade_id}

        if status in ("cancelled", "inactive", "apicancelled"):
            raise FillTimeout(f"Order {ib_order_id} was cancelled (status={status})")

    # Not in the filled/inactive list yet — still pending
    raise FillPending(f"Order {ib_order_id} not yet filled")
