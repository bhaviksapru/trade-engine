"""
Signal Lambda - receives NinjaTrader signals, starts Step Functions execution.
Triggered by API Gateway POST /signal.

Fixes applied:
  - execution_arn now stored in DynamoDB so dead_man / portfolio_risk can stop runaway executions
  - trade_id now includes microseconds + 6-char uuid suffix to prevent same-second collisions
"""
import json
import os
import uuid
import boto3
import logging
from datetime import datetime, timezone

logger = logging.getLogger()
logger.setLevel(logging.INFO)

sf           = boto3.client("stepfunctions")
dynamodb     = boto3.resource("dynamodb")
config_table = dynamodb.Table(os.environ["CONFIG_TABLE"])
trades_table = dynamodb.Table(os.environ["TRADES_TABLE"])

TRADE_LIFECYCLE_ARN = os.environ["TRADE_LIFECYCLE_ARN"]


def handler(event, context):
    try:
        body = json.loads(event.get("body", "{}"))
    except json.JSONDecodeError:
        return response(400, {"error": "Invalid JSON body"})

    # Validate required fields
    required = ["strategy_id", "symbol", "side", "quantity"]
    missing = [f for f in required if not body.get(f)]
    if missing:
        return response(400, {"error": f"Missing fields: {missing}"})

    side = body["side"].upper()
    if side not in ("BUY", "SELL"):
        return response(400, {"error": "side must be BUY or SELL"})

    quantity = int(body.get("quantity", 0))
    if quantity <= 0:
        return response(400, {"error": "quantity must be positive"})

    order_type  = body.get("order_type", "MKT").upper()
    limit_price = body.get("limit_price")
    if order_type == "LMT" and not limit_price:
        return response(400, {"error": "limit_price required for LMT orders"})

    # Check trading_enabled flag
    try:
        result = config_table.get_item(Key={"pk": "trading_enabled"})
        if not result.get("Item", {}).get("value", True):
            logger.warning("Signal received but trading is disabled")
            return response(200, {"status": "paused", "message": "Trading is currently disabled"})
    except Exception as e:
        logger.error(f"Failed to check trading_enabled: {e}")
        return response(503, {"error": "Cannot verify trading status"})

    # Build collision-proof trade ID: timestamp (µs precision) + 6-char random suffix
    now         = datetime.now(timezone.utc)
    ts          = now.strftime("%Y%m%d_%H%M%S") + f"_{now.microsecond:06d}"
    rand_suffix = uuid.uuid4().hex[:6]
    trade_id    = f"trade_{ts}_{body['symbol'].upper()}_{side}_{rand_suffix}"

    # Step Functions input
    sf_input = {
        "trade_id":    trade_id,
        "strategy_id": body["strategy_id"],
        "symbol":      body["symbol"].upper(),
        "side":        side,
        "quantity":    quantity,
        "order_type":  order_type,
        "limit_price": limit_price,
        "comment":     body.get("comment", ""),
        "chain_count": 0,
    }

    try:
        sf_result = sf.start_execution(
            stateMachineArn=TRADE_LIFECYCLE_ARN,
            name=trade_id,
            input=json.dumps(sf_input),
        )
        execution_arn = sf_result["executionArn"]
        logger.info(f"Started execution: {execution_arn} for trade {trade_id}")
    except sf.exceptions.ExecutionAlreadyExists:
        return response(409, {"error": f"Trade {trade_id} already exists"})
    except Exception as e:
        logger.error(f"Failed to start Step Functions: {e}")
        return response(500, {"error": "Failed to start trade execution"})

    # FIX: Persist initial trade record with execution_arn so dead_man /
    # portfolio_risk can stop runaway Step Functions executions in emergencies.
    try:
        trades_table.put_item(Item={
            "trade_id":      trade_id,
            "strategy_id":   body["strategy_id"],
            "symbol":        body["symbol"].upper(),
            "side":          side,
            "quantity":      quantity,
            "order_type":    order_type,
            "execution_arn": execution_arn,
            "status":        "PENDING",
            "created_at":    now.isoformat(),
        })
    except Exception as e:
        # Log but don't fail — SF is already running. Emergency close will
        # still hit IB; it just cannot halt the SF execution cleanly.
        logger.error(f"Failed to write initial trade record: {e}")

    return response(202, {
        "status":        "accepted",
        "trade_id":      trade_id,
        "execution_arn": execution_arn,
    })


def response(status_code, body):
    return {
        "statusCode": status_code,
        "headers":    {"Content-Type": "application/json"},
        "body":       json.dumps(body),
    }
