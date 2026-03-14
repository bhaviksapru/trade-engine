"""
Risk Check Lambda - pre-trade validation before any order reaches IB.
Called as first state in trade_lifecycle Standard Workflow.
Returns { "approved": true/false, "reason": "..." }

Fixes applied:
  - Market hours now uses zoneinfo with America/New_York for correct DST handling.
    Old code used a fixed UTC range (13:00-21:00) that allowed pre/after-hours trades.
  - Also checks weekends.
"""
import os
import boto3
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb     = boto3.resource("dynamodb")
config_table = dynamodb.Table(os.environ["CONFIG_TABLE"])
trades_table = dynamodb.Table(os.environ["TRADES_TABLE"])

DEFAULT_MAX_POSITION_SIZE  = int(os.environ.get("MAX_POSITION_SIZE", "10"))
DEFAULT_MAX_DAILY_LOSS_USD = float(os.environ.get("MAX_DAILY_LOSS_USD", "500"))

ET_TZ = ZoneInfo("America/New_York")

# CME MES RTH core session: 09:30–16:00 ET, Monday–Friday.
# MES technically trades 23h/day but NinjaTrader strategies target RTH.
MARKET_OPEN_HOUR  = 9
MARKET_OPEN_MIN   = 30
MARKET_CLOSE_HOUR = 16
MARKET_CLOSE_MIN  = 0


def _is_rth() -> bool:
    """Return True if current time falls within MES RTH on a weekday."""
    now_et = datetime.now(ET_TZ)
    if now_et.weekday() >= 5:          # Saturday=5, Sunday=6
        return False
    open_t  = now_et.replace(hour=MARKET_OPEN_HOUR,  minute=MARKET_OPEN_MIN,  second=0, microsecond=0)
    close_t = now_et.replace(hour=MARKET_CLOSE_HOUR, minute=MARKET_CLOSE_MIN, second=0, microsecond=0)
    return open_t <= now_et < close_t


def handler(event, context):
    trade_id    = event["trade_id"]
    strategy_id = event["strategy_id"]
    symbol      = event["symbol"]
    side        = event["side"]
    quantity    = int(event["quantity"])

    logger.info(f"[{trade_id}] Risk check: {side} {quantity} {symbol} ({strategy_id})")

    # --- 1. Trading enabled flag ---
    try:
        result = config_table.get_item(Key={"pk": "trading_enabled"})
        if not result.get("Item", {}).get("value", True):
            reason = result.get("Item", {}).get("reason", "trading_disabled")
            return reject(trade_id, f"Trading is disabled: {reason}")
    except Exception as e:
        logger.error(f"Failed to check trading_enabled: {e}")
        return reject(trade_id, "Cannot verify trading status - fail safe")

    # --- 2. Trading hours check (ET, DST-aware) ---
    if not _is_rth():
        now_et = datetime.now(ET_TZ)
        return reject(trade_id, f"Outside RTH: {now_et.strftime('%a %H:%M %Z')}")

    # --- 3. Load live risk parameters ---
    try:
        risk_item      = config_table.get_item(Key={"pk": "risk_parameters"}).get("Item", {})
        max_daily_loss = float(risk_item.get("max_daily_loss_usd", DEFAULT_MAX_DAILY_LOSS_USD))
        max_position   = int(risk_item.get("max_position_size",   DEFAULT_MAX_POSITION_SIZE))
    except Exception as e:
        logger.error(f"Failed to read risk_parameters: {e}")
        max_daily_loss = DEFAULT_MAX_DAILY_LOSS_USD
        max_position   = DEFAULT_MAX_POSITION_SIZE

    # --- 4. Daily loss limit ---
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        pnl_item  = config_table.get_item(Key={"pk": f"daily_pnl#{today}"}).get("Item", {})
        daily_pnl = float(pnl_item.get("total_pnl", 0))
        if daily_pnl <= -abs(max_daily_loss):
            return reject(trade_id, f"Daily loss limit hit: ${daily_pnl:.2f} (limit: ${max_daily_loss:.2f})")
    except Exception as e:
        logger.warning(f"Could not read daily P&L: {e}")

    # --- 5. Open position count ---
    try:
        result     = trades_table.query(
            IndexName="StatusIndex",
            KeyConditionExpression="status = :s",
            ExpressionAttributeValues={":s": "OPEN"},
            Select="COUNT",
        )
        open_count = result.get("Count", 0)
        if open_count >= max_position:
            return reject(trade_id, f"Max open positions reached: {open_count}/{max_position}")
    except Exception as e:
        logger.warning(f"Could not count open positions: {e}")

    # --- 6. Quantity sanity check ---
    if quantity <= 0 or quantity > max_position:
        return reject(trade_id, f"Invalid quantity: {quantity} (max: {max_position})")

    logger.info(f"[{trade_id}] Risk check PASSED")
    return {"approved": True, "trade_id": trade_id}


def reject(trade_id: str, reason: str) -> dict:
    logger.warning(f"[{trade_id}] Risk check REJECTED: {reason}")
    return {"approved": False, "trade_id": trade_id, "reason": reason}
