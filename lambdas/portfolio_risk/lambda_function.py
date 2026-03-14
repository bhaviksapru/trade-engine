"""
Portfolio Risk Lambda - cross-trade exposure monitor. Runs every 60s during market hours.
If daily loss limit is exceeded across all strategies:
  1. Stops all running Step Functions executions
  2. Closes all open positions at market
  3. Disables trading
  4. Fires SNS alert
"""
import os, json, boto3, httpx, logging
from datetime import datetime, timezone

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb     = boto3.resource("dynamodb")
sf           = boto3.client("stepfunctions")
sns_client   = boto3.client("sns")
trades_table = dynamodb.Table(os.environ["TRADES_TABLE"])
config_table = dynamodb.Table(os.environ["CONFIG_TABLE"])
CP_URL       = os.environ["CP_GATEWAY_URL"]
SNS_TOPIC    = os.environ["SNS_TOPIC_ARN"]
MAX_DAILY_LOSS_DEFAULT = float(os.environ.get("MAX_DAILY_LOSS_USD", "500"))


def handler(event, context):
    now   = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")

    # Load live risk parameters
    try:
        risk_item    = config_table.get_item(Key={"pk": "risk_parameters"}).get("Item", {})
        max_daily_loss = float(risk_item.get("max_daily_loss_usd", MAX_DAILY_LOSS_DEFAULT))
    except Exception:
        max_daily_loss = MAX_DAILY_LOSS_DEFAULT

    # Get today's P&L
    try:
        pnl_item   = config_table.get_item(Key={"pk": f"daily_pnl#{today}"}).get("Item", {})
        daily_pnl  = float(pnl_item.get("total_pnl", 0))
    except Exception:
        daily_pnl = 0.0

    # Add unrealized P&L from open positions
    try:
        result = trades_table.query(
            IndexName="StatusIndex",
            KeyConditionExpression="status = :s",
            ExpressionAttributeValues={":s": "OPEN"},
        )
        open_trades    = result.get("Items", [])
        unrealized_pnl = sum(float(t.get("unrealized_pnl", 0)) for t in open_trades)
        total_pnl      = daily_pnl + unrealized_pnl
    except Exception as e:
        logger.error(f"Failed to scan open trades: {e}")
        return {"status": "error", "message": str(e)}

    logger.info(
        f"Portfolio risk check - realized={daily_pnl:.2f} unrealized={unrealized_pnl:.2f} "
        f"total={total_pnl:.2f} limit={max_daily_loss:.2f}"
    )

    if total_pnl > -abs(max_daily_loss):
        return {
            "status":         "ok",
            "daily_pnl":      daily_pnl,
            "unrealized_pnl": unrealized_pnl,
            "total_pnl":      total_pnl,
            "open_trades":    len(open_trades),
        }

    # --- LIMIT BREACHED - emergency shutdown ---
    logger.warning(
        f"DAILY LOSS LIMIT BREACHED: ${total_pnl:.2f} (limit: ${max_daily_loss:.2f}). "
        f"Closing {len(open_trades)} open position(s)."
    )

    closed, failed = [], []
    for trade in open_trades:
        trade_id = trade["trade_id"]
        try:
            # Stop SF execution
            if trade.get("execution_arn"):
                sf.stop_execution(
                    executionArn=trade["execution_arn"],
                    cause="PortfolioRiskLimit: daily loss limit exceeded"
                )
            # Market close
            _market_close(trade)
            # Update DynamoDB
            trades_table.update_item(
                Key={"trade_id": trade_id},
                UpdateExpression="SET #s = :s, exit_reason = :r, close_time = :t",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={
                    ":s": "CLOSED", ":r": "PORTFOLIO_RISK_LIMIT",
                    ":t": datetime.now(timezone.utc).isoformat(),
                },
            )
            closed.append(trade_id)
        except Exception as e:
            logger.error(f"Failed to close {trade_id}: {e}")
            failed.append({"trade_id": trade_id, "error": str(e)})

    # Disable trading
    config_table.put_item(Item={
        "pk": "trading_enabled", "value": False,
        "reason": "portfolio_risk_limit",
    })

    # Alert
    message = (
        f"PORTFOLIO RISK LIMIT HIT\n"
        f"Total P&L: ${total_pnl:.2f} (limit: -${abs(max_daily_loss):.2f})\n"
        f"Closed: {len(closed)} positions\n"
        f"Failed: {len(failed)}\n"
        f"Trading disabled. Check dashboard."
    )
    try:
        sns_client.publish(TopicArn=SNS_TOPIC, Message=message, Subject="Trade Engine - Risk Limit")
    except Exception as e:
        logger.error(f"SNS alert failed: {e}")

    return {
        "status":    "limit_triggered",
        "total_pnl": total_pnl,
        "closed":    closed,
        "failed":    failed,
    }


def _market_close(trade: dict):
    account_id = _get_account_id()
    conid      = _get_conid(trade["symbol"])
    close_side = "SELL" if trade["side"] == "BUY" else "BUY"
    httpx.post(
        f"{CP_URL}/v1/api/iserver/account/{account_id}/orders",
        json={"orders": [{
            "conid": int(conid), "orderType": "MKT",
            "side": close_side, "quantity": int(trade["quantity"]), "tif": "DAY",
        }]},
        verify=False, timeout=10
    ).raise_for_status()


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
        raise ValueError(f"No conid for {symbol}")
    return str(data[0]["conid"])
