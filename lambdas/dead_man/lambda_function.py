"""
Dead Man Lambda — emergency safety net. Runs every 5 minutes, 24/7.
Scans DynamoDB for open positions where last_heartbeat is stale (>15min).
If found: forcefully closes position at IB and stops the Step Functions execution.
This is the last line of defense if all other components fail.
"""
import os
import json
import boto3
import httpx
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource("dynamodb")
trades_table = dynamodb.Table(os.environ["TRADES_TABLE"])
config_table = dynamodb.Table(os.environ["CONFIG_TABLE"])
sf = boto3.client("stepfunctions")
sns = boto3.client("sns")

CP_GATEWAY_URL = os.environ["CP_GATEWAY_URL"]
SNS_TOPIC_ARN = os.environ["SNS_TOPIC_ARN"]
STALE_THRESHOLD_MINUTES = int(os.environ.get("STALE_THRESHOLD_MINUTES", "15"))


def handler(event, context):
    now = datetime.now(timezone.utc)
    stale_cutoff = now - timedelta(minutes=STALE_THRESHOLD_MINUTES)

    # Scan for open trades
    try:
        result = trades_table.query(
            IndexName="StatusIndex",
            KeyConditionExpression="status = :s",
            ExpressionAttributeValues={":s": "OPEN"},
        )
        open_trades = result.get("Items", [])
    except Exception as e:
        logger.error(f"DynamoDB scan failed: {e}")
        return {"status": "error", "message": str(e)}

    if not open_trades:
        logger.info("Dead man check: no open trades")
        return {"status": "ok", "open_trades": 0}

    orphaned = []
    for trade in open_trades:
        heartbeat_str = trade.get("last_heartbeat")
        if not heartbeat_str:
            orphaned.append(trade)
            continue
        heartbeat = datetime.fromisoformat(heartbeat_str.replace("Z", "+00:00"))
        if heartbeat < stale_cutoff:
            orphaned.append(trade)

    if not orphaned:
        logger.info(f"Dead man check: {len(open_trades)} open trade(s), all healthy")
        return {"status": "ok", "open_trades": len(open_trades)}

    logger.warning(f"Dead man triggered: {len(orphaned)} orphaned position(s) found")

    closed = []
    failed = []

    for trade in orphaned:
        trade_id = trade["trade_id"]
        symbol = trade["symbol"]
        side = trade["side"]
        quantity = int(trade.get("quantity", 1))
        execution_arn = trade.get("execution_arn")

        logger.warning(f"Closing orphaned trade: {trade_id} — {side} {quantity} {symbol}")

        # 1. Stop Step Functions execution if running
        if execution_arn:
            try:
                sf.stop_execution(
                    executionArn=execution_arn,
                    cause="DeadManLambda: stale heartbeat"
                )
                logger.info(f"Stopped SF execution: {execution_arn}")
            except Exception as e:
                logger.warning(f"Could not stop execution {execution_arn}: {e}")

        # 2. Place market close order at IB
        close_side = "SELL" if side == "BUY" else "BUY"
        try:
            account_id = get_account_id()
            conid = get_conid(symbol)
            order_resp = httpx.post(
                f"{CP_GATEWAY_URL}/v1/api/iserver/account/{account_id}/orders",
                json={"orders": [{
                    "conid": int(conid),
                    "orderType": "MKT",
                    "side": close_side,
                    "quantity": quantity,
                    "tif": "DAY",
                }]},
                verify=False, timeout=10
            )
            order_resp.raise_for_status()
            logger.info(f"Emergency close order placed for {trade_id}")

            # 3. Update DynamoDB
            trades_table.update_item(
                Key={"trade_id": trade_id},
                UpdateExpression="SET #s = :s, exit_reason = :r, close_time = :t",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={
                    ":s": "CLOSED",
                    ":r": "DEAD_MAN_TRIGGERED",
                    ":t": datetime.now(timezone.utc).isoformat(),
                }
            )
            closed.append(trade_id)

        except Exception as e:
            logger.error(f"Emergency close FAILED for {trade_id}: {e}")
            failed.append({"trade_id": trade_id, "error": str(e)})

    # 4. Disable trading and alert
    config_table.put_item(Item={
        "pk": "trading_enabled",
        "value": False,
        "reason": "dead_man_triggered",
    })

    message = (
        f"🚨 DEAD MAN TRIGGERED\n"
        f"Orphaned positions: {len(orphaned)}\n"
        f"Closed: {closed}\n"
        f"Failed to close: {failed}\n"
        f"Trading disabled. Re-enable from dashboard after investigation."
    )
    try:
        sns.publish(TopicArn=SNS_TOPIC_ARN, Message=message, Subject="🚨 Trade Engine Emergency")
        logger.info("Emergency SNS alert sent")
    except Exception as e:
        logger.error(f"SNS alert failed: {e}")

    return {
        "status": "dead_man_triggered",
        "orphaned": len(orphaned),
        "closed": closed,
        "failed": failed,
    }


def get_account_id() -> str:
    resp = httpx.get(f"{CP_GATEWAY_URL}/v1/api/portfolio/accounts", verify=False, timeout=5)
    resp.raise_for_status()
    return resp.json()[0]["accountId"]


def get_conid(symbol: str) -> str:
    resp = httpx.get(
        f"{CP_GATEWAY_URL}/v1/api/iserver/secdef/search",
        params={"symbol": symbol, "secType": "STK"},
        verify=False, timeout=5
    )
    resp.raise_for_status()
    data = resp.json()
    if not data:
        raise ValueError(f"No conid for {symbol}")
    return str(data[0]["conid"])
