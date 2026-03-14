# TradeMonitor (ib_insync) removed.
# The orchestrator now relays orders to the Signal Lambda → Step Functions pipeline.
# All trade lifecycle management (fills, stops, heartbeats, dead-man) lives in Lambdas.
