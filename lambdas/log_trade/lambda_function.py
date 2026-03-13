"""
Log Trade Lambda — final trade record, updates daily P&L, fires SNS alert.
Called as last state in both normal close and rejected/failed paths.
"""
import os, json, boto3, logging
from datetime import datetime, timezone
from decimal import Decimal

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb     = boto3.resource("dynamodb")
sns_client   = boto3.client("sns")
trades_table = dynamodb.Table(os.environ["TRADES_TABLE"])
config_table = dynamodb.Table(os.environ["CONFIG_TABLE"])
SNS_TOPIC    = os.environ["SNS_TOPIC_ARN"]


def handler(event, context):
    trade_id    = event["trade_id"]
    status      = event.get("status", "UNKNOWN")
    strategy_id = event.get("strategy_id", "unknown")
    symbol      = event.get("symbol", "")
    side        = event.get("side", "")
    quantity    = int(event.get("quantity", 0))
    fill_price  = float(event.get("fill_price", 0))
    close_price = float(event.get("close_price", 0))
    exit_reason = event.get("exit_reason", "")
    pnl_usd     = float(event.get("pnl_usd", 0))

    logger.info(f"[{trade_id}] Logging trade — status={status} pnl={pnl_usd}")

    # Update final trade record
    now = datetime.now(timezone.utc).isoformat()
    try:
        trades_table.update_item(
            Key={"trade_id": trade_id},
            UpdateExpression=(
                "SET #s = :status, pnl_usd = :pnl, close_price = :cp, "
                "exit_reason = :er, logged_at = :t"
            ),
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":status": status,
                ":pnl":    str(round(pnl_usd, 2)),
                ":cp":     str(close_price),
                ":er":     exit_reason,
                ":t":      now,
            },
        )
    except Exception as e:
        logger.error(f"Failed to update trade record: {e}")

    # Update daily P&L summary
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if status == "CLOSED" and pnl_usd != 0:
        try:
            is_win = pnl_usd > 0
            config_table.update_item(
                Key={"pk": f"daily_pnl#{today}"},
                UpdateExpression=(
                    "ADD total_pnl :pnl, trade_count :one, "
                    "win_count :win, loss_count :loss "
                    "SET #d = :date"
                ),
                ExpressionAttributeNames={"#d": "date"},
                ExpressionAttributeValues={
                    ":pnl":  Decimal(str(round(pnl_usd, 2))),
                    ":one":  Decimal("1"),
                    ":win":  Decimal("1") if is_win else Decimal("0"),
                    ":loss": Decimal("0") if is_win else Decimal("1"),
                    ":date": today,
                },
            )
        except Exception as e:
            logger.error(f"Failed to update daily P&L: {e}")

    # Send SNS alert if notifications are enabled
    _maybe_notify(trade_id, status, symbol, side, quantity, fill_price, close_price, pnl_usd, exit_reason)

    return {"logged": True, "trade_id": trade_id, "pnl_usd": pnl_usd}


def _maybe_notify(trade_id, status, symbol, side, quantity, fill_price, close_price, pnl, exit_reason):
    try:
        prefs_item = config_table.get_item(Key={"pk": "notification_preferences"}).get("Item", {})
        if not prefs_item.get("enabled", True):
            return
        events = prefs_item.get("events", {})

        should_notify = (
            (status == "CLOSED" and exit_reason == "STOP_HIT"  and events.get("stop_hit",  True)) or
            (status == "CLOSED" and exit_reason == "TP_HIT"    and events.get("tp_hit",    True)) or
            (status == "CLOSED" and events.get("fill",         False)) or
            (status == "REJECTED"                              and events.get("fill",       False))
        )

        if not should_notify:
            return

        emoji  = "🟢" if pnl > 0 else ("🔴" if pnl < 0 else "⚪")
        pnl_str = f"+${pnl:.2f}" if pnl > 0 else f"-${abs(pnl):.2f}"

        message = (
            f"{emoji} Trade Engine — {status}\n"
            f"{side} {quantity} {symbol} [{exit_reason}]\n"
            f"Fill: ${fill_price:.2f} → Close: ${close_price:.2f}\n"
            f"P&L: {pnl_str}\n"
            f"ID: {trade_id[-20:]}"
        )

        sns_client.publish(
            TopicArn=SNS_TOPIC,
            Message=message,
            Subject=f"Trade Engine — {status} {symbol}",
        )
        logger.info(f"[{trade_id}] SNS alert sent")
    except Exception as e:
        logger.error(f"SNS notification failed: {e}")
