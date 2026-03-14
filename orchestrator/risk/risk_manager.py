"""
RiskManager - fast in-process pre-checks before forwarding to Signal Lambda.

Rules enforced here (in addition to the Lambda-side risk_check):
  - Duplicate order cooldown (guards against runaway NinjaTrader signal loops)
  - Projected position size guard (soft limit before the Lambda hard limit)
  - Daily loss check reads live DynamoDB value on every call so the orchestrator
    sees the same realized P&L that the Lambda risk_check sees.

⚠️  SINGLE-REPLICA NOTE:
_positions and _last_order are in-process state. With ECS service count > 1
each replica has independent state, making the soft position limit inaccurate.
For multi-replica deployments replace _positions with a shared DynamoDB or
ElastiCache backend. For a single-operator personal trading setup (one ECS
task) in-process state is sufficient.

The daily loss limit NO LONGER uses in-process _daily_loss state because
trade outcomes arrive asynchronously (Step Functions → Lambda → DynamoDB).
The only source of truth is DynamoDB's daily_pnl#<today> item, which this
class reads directly on every check() call.
"""
import os
import time
import logging
import boto3
from datetime import date, datetime, timezone
from collections import defaultdict

logger = logging.getLogger(__name__)

MAX_POSITION_SIZE      = int(os.getenv("MAX_POSITION_SIZE",      "10"))
MAX_DAILY_LOSS_USD     = float(os.getenv("MAX_DAILY_LOSS",        "500"))
ORDER_COOLDOWN_SECONDS = int(os.getenv("ORDER_COOLDOWN_SECONDS",  "5"))
CONFIG_TABLE           = os.getenv("CONFIG_TABLE", "")


def _get_config_table():
    """Lazy singleton for the DynamoDB config table resource."""
    if not CONFIG_TABLE:
        return None
    dynamodb = boto3.resource("dynamodb")
    return dynamodb.Table(CONFIG_TABLE)


class RiskManager:
    def __init__(self):
        # { (strategy_id, symbol): net_position }
        self._positions:  dict[tuple, int]   = defaultdict(int)
        # { (strategy_id, symbol, side): last_fill_timestamp }
        self._last_order: dict[tuple, float] = {}
        self._config_tbl = _get_config_table()
        logger.info(
            f"RiskManager init — max_pos={MAX_POSITION_SIZE} "
            f"max_daily_loss=${MAX_DAILY_LOSS_USD} "
            f"cooldown={ORDER_COOLDOWN_SECONDS}s "
            f"config_table={'set' if self._config_tbl else 'NOT SET — daily loss check disabled'}"
        )

    def _read_daily_pnl(self, strategy_id: str) -> float:
        """Read today's realized P&L from DynamoDB.

        Uses the same daily_pnl#<date> item that risk_check Lambda writes.
        Falls back to 0.0 if DynamoDB is unreachable so the orchestrator
        never hard-blocks on a transient DB error (Lambda risk_check is the
        authoritative gate anyway).
        """
        if not self._config_tbl:
            return 0.0
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        try:
            item = self._config_tbl.get_item(
                Key={"pk": f"daily_pnl#{today}"}
            ).get("Item", {})
            return float(item.get("total_pnl", 0))
        except Exception as e:
            logger.warning(f"Could not read daily P&L from DynamoDB: {e}")
            return 0.0

    async def check(self, order, side: str) -> str | None:
        """Returns a rejection reason string, or None if the order passes all checks."""
        key = (order.strategy_id, order.symbol)

        # 1. Daily loss check — read live DynamoDB value, not stale in-process state
        daily_pnl = self._read_daily_pnl(order.strategy_id)
        if daily_pnl <= -abs(MAX_DAILY_LOSS_USD):
            return (
                f"Daily loss limit ${MAX_DAILY_LOSS_USD} reached for strategy "
                f"{order.strategy_id} (realized P&L: ${daily_pnl:.2f})"
            )

        # 2. Soft position size guard
        current   = self._positions[key]
        projected = current + order.quantity if side == "BUY" else current - order.quantity
        if abs(projected) > MAX_POSITION_SIZE:
            return (
                f"Position size limit: {order.symbol} would reach {projected} "
                f"(max ±{MAX_POSITION_SIZE})"
            )

        # 3. Cooldown guard — prevents signal-loop runaway
        cooldown_key = (order.strategy_id, order.symbol, side)
        last         = self._last_order.get(cooldown_key, 0)
        if time.time() - last < ORDER_COOLDOWN_SECONDS:
            return (
                f"Order cooldown active for {order.symbol} {side} — "
                f"wait {ORDER_COOLDOWN_SECONDS}s between signals"
            )

        return None

    def record_fill(self, order, side: str):
        """Call after a successful order relay to update in-process position state."""
        key = (order.strategy_id, order.symbol)
        if side == "BUY":
            self._positions[key] += order.quantity
        else:
            self._positions[key] -= order.quantity
        cooldown_key                  = (order.strategy_id, order.symbol, side)
        self._last_order[cooldown_key] = time.time()
