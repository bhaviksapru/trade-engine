"""
Place Order Lambda — sends order to IB via CP Gateway, writes DynamoDB.
Returns { "ib_order_id": "...", "conid": "..." }
"""
import os, json, boto3, httpx, logging
from datetime import datetime, timezone

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb     = boto3.resource("dynamodb")
secretsm     = boto3.client("secretsmanager")
trades_table = dynamodb.Table(os.environ["TRADES_TABLE"])
CP_URL       = os.environ["CP_GATEWAY_URL"]


def handler(event, context):
    trade_id   = event["trade_id"]
    symbol     = event["symbol"]
    side       = event["side"]
    quantity   = int(event["quantity"])
    order_type = event.get("order_type", "MKT")
    limit_price = event.get("limit_price")

    logger.info(f"[{trade_id}] Placing {order_type} {side} {quantity} {symbol}")

    account_id = _get_account_id()
    conid      = _get_conid(symbol)

    order_body = {
        "orders": [{
            "conid":     int(conid),
            "orderType": order_type,
            "side":      side,
            "quantity":  quantity,
            "tif":       "DAY",
        }]
    }
    if order_type == "LMT" and limit_price:
        order_body["orders"][0]["price"] = float(limit_price)

    resp = httpx.post(
        f"{CP_URL}/v1/api/iserver/account/{account_id}/orders",
        json=order_body, verify=False, timeout=10
    )
    resp.raise_for_status()
    data = resp.json()

    # CP Gateway returns a list; first item has the order ID
    ib_order_id = str(data[0].get("order_id") or data[0].get("id", "unknown"))

    trades_table.update_item(
        Key={"trade_id": trade_id},
        UpdateExpression="SET ib_order_id = :o, conid = :c, #s = :s, created_at = :t",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={
            ":o": ib_order_id, ":c": str(conid),
            ":s": "PENDING_FILL",
            ":t": datetime.now(timezone.utc).isoformat(),
        },
    )

    logger.info(f"[{trade_id}] Order placed — ib_order_id={ib_order_id}")
    return {"ib_order_id": ib_order_id, "conid": str(conid), "trade_id": trade_id}


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
