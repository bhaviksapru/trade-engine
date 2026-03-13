import os
import logging
from datetime import date
from collections import defaultdict

logger = logging.getLogger(__name__)

MAX_POSITION_SIZE = int(os.getenv("MAX_POSITION_SIZE", "10"))
MAX_DAILY_LOSS_USD = float(os.getenv("MAX_DAILY_LOSS", "500"))
ORDER_COOLDOWN_SECONDS = int(os.getenv("ORDER_COOLDOWN_SECONDS", "5"))


class RiskManager:
    """
    Enforces per-strategy risk rules before orders reach IB Gateway.

    Rules:
    - Max position size per strategy+symbol
    - Daily loss limit (halts all trading for the session if breached)
    - Duplicate order guard (cooldown window between identical signals)
    """

    def __init__(self):
        # { (strategy_id, symbol): net_position }
        self._positions: dict[tuple, int] = defaultdict(int)
        # { strategy_id: realized_loss_today }
        self._daily_loss: dict[str, float] = defaultdict(float)
        self._loss_date: date = date.today()
        # { (strategy_id, symbol, side): last_fill_timestamp }
        self._last_order: dict[tuple, float] = {}
        logger.info(
            f"RiskManager init — max_pos={MAX_POSITION_SIZE} "
            f"max_daily_loss=${MAX_DAILY_LOSS_USD} "
            f"cooldown={ORDER_COOLDOWN_SECONDS}s"
        )

    def _reset_if_new_day(self):
        today = date.today()
        if today != self._loss_date:
            self._daily_loss.clear()
            self._loss_date = today
            logger.info("New trading day — daily loss counters reset.")

    async def check(self, order, side: str) -> str | None:
        """
        Returns a rejection reason string, or None if the order passes all checks.
        """
        self._reset_if_new_day()
        key = (order.strategy_id, order.symbol)

        # 1. Daily loss halt
        if self._daily_loss[order.strategy_id] >= MAX_DAILY_LOSS_USD:
            return f"Daily loss limit ${MAX_DAILY_LOSS_USD} reached for strategy {order.strategy_id}"

        # 2. Position size limit
        current = self._positions[key]
        projected = current + order.quantity if side == "BUY" else current - order.quantity
        if abs(projected) > MAX_POSITION_SIZE:
            return (
                f"Position size limit: {order.symbol} would reach {projected} "
                f"(max ±{MAX_POSITION_SIZE})"
            )

        # 3. Cooldown guard
        import time
        cooldown_key = (order.strategy_id, order.symbol, side)
        last = self._last_order.get(cooldown_key, 0)
        if time.time() - last < ORDER_COOLDOWN_SECONDS:
            return f"Order cooldown active for {order.symbol} {side} — wait {ORDER_COOLDOWN_SECONDS}s between signals"

        return None

    def record_fill(self, order, side: str):
        """Call after a successful order placement to update internal state."""
        import time
        key = (order.strategy_id, order.symbol)
        if side == "BUY":
            self._positions[key] += order.quantity
        else:
            self._positions[key] -= order.quantity
        cooldown_key = (order.strategy_id, order.symbol, side)
        self._last_order[cooldown_key] = time.time()

    def record_loss(self, strategy_id: str, loss_usd: float):
        """Call from trade monitor when a position closes at a loss."""
        self._daily_loss[strategy_id] += loss_usd
        logger.info(f"[{strategy_id}] Daily loss updated: ${self._daily_loss[strategy_id]:.2f}")
