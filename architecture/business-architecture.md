# Business Architecture

## Problem Statement

NinjaTrader's live trade execution licence costs approximately **$100/month** (~$1,200/year). This fee is charged for the privilege of routing orders through NinjaTrader's brokerage integration layer - a function that is technically replaceable with a custom integration using a broker's own API.

Interactive Brokers provides the **Client Portal API Gateway** - a free, self-hosted REST interface that any application can use to place orders, query positions, and manage risk directly. The trading signal intelligence (technical analysis, strategy logic) remains in NinjaTrader, which is available free for simulation and signal generation.

---

## Value Proposition

| | Before | After |
|---|---|---|
| Monthly cost | ~$100 (NinjaTrader execution) | ~$30 (AWS 3-AZ HA infrastructure) |
| Annual cost | ~$1,200 | ~$360 |
| **Annual saving** | | **~$840** |
| Broker | Interactive Brokers | Interactive Brokers (unchanged) |
| Strategy logic | NinjaTrader C# | NinjaTrader C# (unchanged) |
| Execution | NinjaTrader → IB | AWS → IB directly |
| Risk management | NinjaTrader | Dedicated cloud layer (more flexible) |
| Monitoring | NinjaTrader desktop | Live web dashboard (anywhere) |
| Reliability | Desktop process | Cloud-hosted, crash-resilient |

---

## Stakeholders

| Stakeholder | Role | Concern |
|---|---|---|
| Trader (you) | Owner + operator | Cost, reliability, visibility |
| Interactive Brokers | Broker | Order correctness, compliance |
| AWS | Infrastructure provider | Uptime SLAs |
| NinjaTrader | Signal source | Signal accuracy |

---

## Business Capabilities

```
┌─────────────────────────────────────────────────────────────────┐
│                        TRADE ENGINE                              │
├─────────────┬────────────────┬────────────────┬─────────────────┤
│   SIGNAL    │   EXECUTION    │      RISK      │   MONITORING    │
│  INGESTION  │  MANAGEMENT    │   MANAGEMENT   │   & ALERTING    │
│             │                │                │                 │
│ Receive     │ Route orders   │ Pre-trade      │ Live dashboard  │
│ NinjaTrader │ to IB via CP   │ validation     │ Web-based       │
│ signals     │ Gateway        │                │                 │
│             │                │ Position       │ Real-time P&L   │
│ Validate    │ Fill           │ limits         │                 │
│ source and  │ monitoring     │                │ Trade history   │
│ parameters  │                │ Daily loss     │                 │
│             │ Stop/TP        │ limits         │ Risk metrics    │
│             │ management     │                │                 │
│             │                │ Portfolio      │ SMS alerts      │
│             │                │ drawdown       │                 │
└─────────────┴────────────────┴────────────────┴─────────────────┘
```

---

## Operating Model

**Trading Hours:** 9:00am – 4:30pm ET, Monday–Friday

**Pre-Market (9:00–9:30am):**
- EC2 and Fargate start automatically
- CP Gateway authenticates with IBKR
- System health verified before market open

**Market Hours (9:30am–4:00pm):**
- NinjaTrader fires signals
- AWS executes, monitors, and manages all positions
- Dashboard provides real-time visibility

**Post-Market (4:00–4:30pm):**
- All positions should be flat before 4:00pm
- System logs final P&L
- EC2 and Fargate stop automatically at 4:30pm

**Off-Hours:**
- All compute stops (cost saving)
- Dead man Lambda continues running (safety net - negligible cost)
- DynamoDB retains all trade history

---

## Risk Posture

| Risk | Mitigation |
|---|---|
| Cloud infrastructure failure mid-trade | Step Functions persists state; dead man Lambda closes positions |
| CP Gateway authentication failure | Startup Lambda retries with alerts; trading disabled if auth fails |
| Signal misfire (wrong parameters) | Pre-trade Lambda validates all parameters before any order |
| Daily loss runaway | Daily loss limit in DynamoDB; all new signals rejected if limit reached |
| Position left open overnight | Dead man Lambda closes any position older than 15min with no heartbeat |
| Unauthorised access | Cognito (Google), API key + IP restriction, private VPC, least-privilege IAM |
