# Data Architecture

## DynamoDB Tables

### Table 1: `trade-engine-trades`

Primary store for all trade lifecycle data.

```
Partition Key: trade_id  (String)  e.g. "trade_20240315_143022_ES_BUY"
Sort Key:      (none)

Attributes:
  trade_id          String   "trade_20240315_143022_ES_BUY"
  strategy_id       String   "MyStrategy_v1"
  symbol            String   "ES"
  side              String   "BUY" | "SELL"
  quantity          Number   2
  order_type        String   "MKT" | "LMT"
  limit_price       Number   (optional)
  status            String   "PENDING" | "FILLED" | "OPEN" | "CLOSED" | "REJECTED" | "CANCELLED"
  ib_order_id       String   IB-assigned order ID
  fill_price        Number   actual fill price
  fill_time         String   ISO timestamp
  stop_loss_price   Number
  stop_order_id     String   IB stop order ID
  close_price       Number   exit price
  close_time        String   ISO timestamp
  exit_reason       String   "STOP_HIT" | "TP_HIT" | "TIMEOUT" | "MANUAL" | "EMERGENCY"
  pnl_usd           Number   realized P&L in USD
  execution_arn     String   Step Functions execution ARN
  last_heartbeat    String   ISO timestamp (updated by check_price Lambda)
  created_at        String   ISO timestamp

GSI 1: StatusIndex
  Partition Key: status
  Sort Key:      created_at
  → Query all OPEN trades efficiently (used by portfolio_risk + dead_man Lambdas)

GSI 2: StrategyIndex
  Partition Key: strategy_id
  Sort Key:      created_at
  → Query trades by strategy (used by dashboard)
```

### Table 2: `trade-engine-config`

Key-value store for system configuration and state flags. Updated live from the dashboard.

```
Partition Key: pk  (String)
Sort Key:      (none)

Records:

  pk: "trading_enabled"
    value: Boolean  true | false
    updated_at: String
    updated_by: String  "dashboard" | "portfolio_risk" | "system"

  pk: "notification_preferences"
    enabled: Boolean
    phone: String
    events: Map {
      fill:              Boolean
      stop_hit:          Boolean
      tp_hit:            Boolean
      daily_loss_limit:  Boolean
      emergency_close:   Boolean
      auth_failure:      Boolean
    }

  pk: "risk_parameters"
    max_position_size:   Number   (per strategy per symbol)
    max_daily_loss_usd:  Number
    order_cooldown_secs: Number
    updated_at:          String

  pk: "daily_pnl#2024-03-15"        (one record per trading day)
    date:          String   "2024-03-15"
    total_pnl:     Number
    trade_count:   Number
    win_count:     Number
    loss_count:    Number
    max_drawdown:  Number
```

### Table 3: `trade-engine-positions`

Current open positions - maintained by fill and close Lambdas. Fast read path for dashboard.

```
Partition Key: strategy_id  (String)
Sort Key:      symbol        (String)

Attributes:
  strategy_id      String
  symbol           String
  net_position     Number   positive = long, negative = short
  avg_entry_price  Number
  unrealized_pnl   Number   updated by check_price Lambda on each cycle
  open_trade_ids   List     list of trade_ids contributing to this position
  last_updated     String   ISO timestamp
```

---

## Data Flows

### Signal → Fill

```
NinjaTrader
  POST /signal { strategy_id, symbol, side, quantity }
    ↓
Signal Lambda
  → writes to trades table: status=PENDING
  → starts Step Functions execution with trade_id
    ↓
risk_check Lambda
  → reads config table: trading_enabled, risk_parameters
  → reads trades table (StatusIndex): count of OPEN trades
  → reads daily_pnl table: today's loss
  → decision: PASS or REJECT
    ↓ (PASS)
place_order Lambda
  → calls CP Gateway REST
  → receives ib_order_id
  → updates trades table: ib_order_id, status=PENDING_FILL
    ↓
wait_for_fill Lambda
  → polls CP Gateway every 2s
  → on fill: updates trades table: fill_price, fill_time, status=FILLED
  → updates positions table: net_position, avg_entry_price
    ↓
set_stop Lambda
  → calls CP Gateway: places stop order
  → updates trades table: stop_loss_price, stop_order_id, status=OPEN
```

### Monitoring Loop (Express Workflow)

```
check_price Lambda (every 5s)
  → calls CP Gateway: GET /marketdata/snapshot
  → updates trades table: last_heartbeat, unrealized_pnl
  → publishes to EventBridge: { trade_id, symbol, current_price, unrealized_pnl }
    ↓
EventBridge
  → FastAPI dashboard WebSocket subscriber receives event
  → pushes to connected browser clients
    ↓
Choice state evaluation
  → stop hit / TP hit / timeout → exit Express WF with reason
```

### Close → Log

```
close_position Lambda
  → calls CP Gateway: market close order
  → updates trades table: close_price, close_time, exit_reason, status=CLOSED, pnl_usd
  → updates positions table: net_position = 0, clears open_trade_ids
    ↓
log_trade Lambda
  → updates daily_pnl table: total_pnl, trade_count, win/loss count
  → reads notification_preferences
  → if notify: SNS publish → SMS to phone
```

---

## EventBridge Events Schema

Bus name: `trade-engine-events`

### price-update event
```json
{
  "source": "trade-engine.check-price",
  "detail-type": "PriceUpdate",
  "detail": {
    "trade_id": "trade_20240315_143022_ES_BUY",
    "symbol": "ES",
    "current_price": 5234.50,
    "bid": 5234.25,
    "ask": 5234.75,
    "unrealized_pnl": 340.00,
    "stop_loss_price": 5210.00,
    "timestamp": "2024-03-15T14:32:00Z"
  }
}
```

### trade-lifecycle event
```json
{
  "source": "trade-engine.lifecycle",
  "detail-type": "TradeStateChange",
  "detail": {
    "trade_id": "trade_20240315_143022_ES_BUY",
    "from_state": "OPEN",
    "to_state": "CLOSED",
    "exit_reason": "TP_HIT",
    "pnl_usd": 187.50,
    "timestamp": "2024-03-15T15:14:22Z"
  }
}
```

---

## Data Retention

| Table | Retention policy |
|---|---|
| trade-engine-trades | Forever (trade history is valuable) |
| trade-engine-config | Forever (1 record per key, small) |
| trade-engine-positions | Current only - cleared on close |
| daily_pnl records | Forever (small, ~250 records/year) |
| CloudWatch Logs | 30 days CloudFormation sets this |
| EventBridge events | Consumed in real-time, not stored |

Point-in-time recovery (PITR) is enabled on `trade-engine-trades` - you can restore to any point within 35 days.
