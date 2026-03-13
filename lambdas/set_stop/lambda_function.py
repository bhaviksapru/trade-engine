"""
Set Stop Lambda — places protective stop order and calculates take-profit level.
Called after fill confirmation. Updates DynamoDB with stop details.
Returns { "stop_loss_price": ..., "take_profit_price": ..., "stop_order_id": ... }
"""
import os, boto3, httpx, logging
from datetime import datetime, timezone

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb     = boto3.resource("dynamodb")
config_table = dynamodb.Table(os.environ["CONFIG_TABLE"])
trades_table = dynamodb.Table(os.environ["TRADES_TABLE"])
CP_URL       = os.environ["CP_GATEWAY_URL"]

# Risk/reward defaults — ideally loaded per-strategy from DynamoDB
STOP_TICKS        = int(os.environ.get("STOP_LOSS_TICKS", "14"))
TP_TICKS          = int(os.environ.get("TAKE_PROFIT_TICKS", "18"))
TICK_SIZE         = float(os.environ.get("TICK_SIZE", "0.25"))  # MES/ES tick
MAX_TRADE_MINUTES = int(os.environ.get("MAX_TRADE_DURATION_MINUTES", "120"))


def handler(event, context):
    trade_id   = event["trade_id"]
    symbol     = event["symbol"]
    side       = event["side"]
    quantity   = int(event["quantity"])
    fill_price = float(event["fill_price"])

    logger.info(f"[{trade_id}] Setting stop — {side} {quantity} {symbol} filled @ {fill_price}")

    if side == "BUY":
        stop_price = fill_price - (STOP_TICKS * TICK_SIZE)
        tp_price   = fill_price + (TP_TICKS   * TICK_SIZE)
        stop_side  = "SELL"
    else:
        stop_price = fill_price + (STOP_TICKS * TICK_SIZE)
        tp_price   = fill_price - (TP_TICKS   * TICK_SIZE)
        stop_side  = "BUY"

    # Round to tick size
    stop_price = round(round(stop_price / TICK_SIZE) * TICK_SIZE, 4)
    tp_price   = round(round(tp_price   / TICK_SIZE) * TICK_SIZE, 4)

    account_id = _get_account_id()
    conid      = _get_conid(symbol)

    stop_resp = httpx.post(
        f"{CP_URL}/v1/api/iserver/account/{account_id}/orders",
        json={"orders": [{
            "conid":      int(conid),
            "orderType":  "STP",
            "side":       stop_side,
            "quantity":   quantity,
            "price":      stop_price,
            "tif":        "GTC",
        }]},
        verify=False, timeout=10
    )
    stop_resp.raise_for_status()
    stop_order_id = str(stop_resp.json()[0].get("order_id", "unknown"))

    trades_table.update_item(
        Key={"trade_id": trade_id},
        UpdateExpression=(
            "SET stop_loss_price = :sl, take_profit_price = :tp, "
            "stop_order_id = :so, #s = :status, updated_at = :t"
        ),
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={
            ":sl":     str(stop_price),
            ":tp":     str(tp_price),
            ":so":     stop_order_id,
            ":status": "OPEN",
            ":t":      datetime.now(timezone.utc).isoformat(),
        },
    )

    logger.info(f"[{trade_id}] Stop placed @ {stop_price} | TP target @ {tp_price}")
    return {
        "stop_loss_price":   stop_price,
        "take_profit_price": tp_price,
        "stop_order_id":     stop_order_id,
        "trade_id":          trade_id,
    }


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
