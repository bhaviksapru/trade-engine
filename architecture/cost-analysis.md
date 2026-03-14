# Cost Analysis

## Assumptions

- **Trading days per month:** 21 (US market average)
- **Trading hours:** 9:00am – 4:30pm ET = 7.5hrs/day with buffer
- **Compute hours/month:** 21 × 7.5 = 157.5 hours
- **Trades per day:** 20
- **Trades per month:** 420
- **AWS Region:** us-east-2 (Ohio) — generally cheapest US region
- **Pricing:** on-demand, as of 2024 (check aws.amazon.com/pricing for current rates)

---

## Compute

### EC2 t3.small (CP Gateway)

The CP Gateway is a Java process requiring ~1GB RAM minimum. t3.small has 2GB RAM and 2 vCPUs.

```
On-demand rate:     $0.0208/hour
Hours/month:        157.5
Monthly cost:       $0.0208 × 157.5 = $3.28

vs always-on:       $0.0208 × 720 = $14.98/month
Saving from schedule: $11.70/month
```

### ECS Fargate — FastAPI Dashboard

0.25 vCPU, 0.5 GB memory (sufficient for dashboard API + WebSocket)

```
vCPU rate:    $0.04048/vCPU-hour
Memory rate:  $0.004445/GB-hour

vCPU cost:    $0.04048 × 0.25 × 157.5 = $1.59
Memory cost:  $0.004445 × 0.5 × 157.5 = $0.35
Monthly:      $1.94

vs always-on: $7.50/month
Saving:       $5.56/month
```

---

## Step Functions

### Standard Workflow (outer trade lifecycle)

~8 state transitions per trade:
PENDING → RISK_CHECK → PLACE_ORDER → WAIT_FILL → SET_STOP → MONITORING → CLOSE → LOG

```
Transitions/month:  420 trades × 8 = 3,360
Free tier:          4,000/month
Monthly cost:       $0.00  (within free tier)

Note: first 4,000 transitions/month are always free
```

### Express Workflow (nested monitoring loop)

Price checked every 5 seconds. 2 transitions per check (CHECK_PRICE + EVALUATE).
Average trade duration: 30 minutes = 360 checks × 2 = 720 transitions/trade.

```
Transitions/month:  420 × 720 = 302,400
Rate:               $1.00/million transitions
Transition cost:    $0.30

Duration cost:      420 trades × 30min × 64MB = 806,400 GB-seconds
Rate:               $0.00001/GB-second
Duration cost:      $0.81 (but first 300,000 GB-seconds free)
Effective cost:     ~$0.50

Monthly total:      ~$0.80
```

---

## API Gateway

HTTP API (cheaper than REST API) for signal endpoint + FastAPI proxy.

```
Requests/month:    420 signals + ~500 dashboard calls = ~1,000
Free tier:         1,000,000 requests/month
Monthly cost:      $0.00  (entirely within free tier)
```

---

## Application Load Balancer

ALB runs continuously (no scheduler — needed for any-hour dashboard access).

```
Hourly rate:       $0.008/hour
Hours/month:       720
Fixed cost:        $0.008 × 720 = $5.76

LCU charges:       ~$0.50 (minimal traffic)
Monthly total:     $6.26
```

Note: This is the largest fixed cost. If dashboard access only needed during market hours,
you could schedule ALB deletion/recreation to save ~$4/month — not worth the complexity.

---

## Lambda Functions

11 Lambda functions total. Most run only during market hours (157.5hrs/month).

| Lambda | Invocations/month | Avg duration | Memory |
|---|---|---|---|
| signal | 420 | 500ms | 128MB |
| risk_check | 420 | 300ms | 128MB |
| place_order | 420 | 800ms | 256MB |
| wait_for_fill | 420 | 2000ms (avg) | 128MB |
| set_stop | 420 | 500ms | 128MB |
| check_price | 302,400 | 200ms | 128MB |
| close_position | 420 | 500ms | 128MB |
| log_trade | 420 | 300ms | 128MB |
| tickle | 8,505 (every 55s × 157.5hrs) | 200ms | 128MB |
| portfolio_risk | 9,450 (every 60s × 157.5hrs) | 300ms | 128MB |
| dead_man | 8,748 (every 5min × 720hrs — runs 24/7) | 300ms | 128MB |

```
Total GB-seconds: ~85,000
Free tier:        400,000 GB-seconds/month
Invocation free:  1,000,000/month

Monthly cost:     $0.00  (entirely within free tier)

Note: Lambda is effectively free at this scale.
```

---

## DynamoDB

On-demand pricing (no reserved capacity needed at this volume).

```
Writes:
  Per trade lifecycle: ~50 writes (state updates, heartbeats, log)
  420 trades × 50 = 21,000 writes
  Config updates: ~100/month
  Total writes: ~21,100/month
  Rate: $1.25/million WCU = $0.03

Reads:
  Dashboard queries: ~500/day × 21 = 10,500
  Lambda reads: ~5,000/month
  Total reads: ~15,500/month
  Rate: $0.25/million RCU = $0.004

Storage:
  Trades table: ~420 records/month × ~2KB = ~840KB/month growth
  Year 1 total: ~10MB
  Rate: $0.25/GB = $0.003

Monthly total: ~$0.04  (negligible)
```

---

## EventBridge

Custom event bus for price updates and trade state changes.

```
Events/month:
  Price updates: 302,400
  Trade events:  420 × 5 = 2,100
  Total: ~304,500

Free tier: 1,000,000 events/month (custom buses)
Monthly cost: $0.00
```

---

## CloudWatch Logs

Log retention set to 30 days in Terraform.

```
Log groups:
  /aws/lambda/trade-engine-* (11 functions)
  /ecs/trade-engine-dashboard
  /aws/ec2/cp-gateway

Estimated ingestion: ~2GB/month
Rate: $0.50/GB ingestion
Ingestion cost: $1.00

Storage (30-day rolling, ~2GB at steady state):
Rate: $0.03/GB
Storage cost: $0.06

Monthly total: ~$1.06
```

---

## Secrets Manager

```
Secrets:
  trade-engine/ib-credentials   (IB username, password, account ID, mode)
  trade-engine/api-key           (NinjaTrader API key)
  trade-engine/cognito-secret    (Cognito app client secret)
  trade-engine/google-oauth      (Google client ID + secret)

Rate: $0.40/secret/month
4 × $0.40 = $1.60

API calls: ~500/month (well within included 10,000/month per secret)

Monthly total: $1.60
```

---

## S3 + CloudFront

Static dashboard files (~50KB total).

```
S3:
  Storage: 50KB (negligible)
  GET requests: ~500/month
  Monthly: ~$0.01

CloudFront:
  Data transfer: ~25MB/month (50KB × 500 loads)
  HTTPS requests: ~2,500/month
  First 1TB transfer: $0.085/GB = $0.002
  First 10M requests free
  Monthly: ~$0.01

Combined: ~$0.02
```

---

## NAT Gateway

Three NAT Gateways (one per AZ) are required for the HA deployment.
VPC Endpoints handle all AWS service traffic without NAT; only CP Gateway → IBKR
traffic flows through NAT.

```
IBKR traffic: ~50MB/day × 21 days = ~1GB/month (concentrated in AZ-a where EC2 lives)

NAT Gateway hours (3 GWs × 157.5hrs): 3 × $0.045/hour × 157.5hrs = $21.26
Data processed: 1GB × $0.045/GB = $0.05

Note: Two of the three NAT Gateways (AZ-b, AZ-c) carry near-zero data because
the CP Gateway EC2 sits in AZ-a. Their cost is purely the hourly charge.
They exist to ensure Lambda and Fargate egress remains healthy if AZ-a NAT fails.
Total: $21.31

Trade-off: +$14.17/month vs the single-NAT design for full AZ-level egress HA.
```

---

## Monthly Cost Summary

| Service | Cost | Notes |
|---|---|---|
| ALB | $6.26 | Largest fixed cost, runs 24/7 |
| NAT Gateway (×3) | $21.31 | One per AZ for HA egress isolation |
| EC2 t3.small | $3.28 | Market hours only |
| ECS Fargate | $1.94 | Market hours only |
| Secrets Manager | $1.60 | Fixed per secret |
| CloudWatch Logs | $1.06 | 30-day retention |
| Step Functions Express | $0.80 | Monitoring loop |
| Step Functions Standard | $0.00 | Free tier |
| Lambda | $0.00 | Free tier |
| DynamoDB | $0.04 | On-demand, low volume |
| API Gateway | $0.00 | Free tier |
| EventBridge | $0.00 | Free tier |
| S3 + CloudFront | $0.02 | Tiny static site |
| **Total** | **~$36.28** | |

---

## Annual Comparison

| | Monthly | Annual |
|---|---|---|
| NinjaTrader execution licence | ~$100 | ~$1,200 |
| trade-engine (AWS, 3-AZ HA) | ~$36 | ~$432 |
| **Saving** | **~$64** | **~$768** |

Break-even: infrastructure pays for itself in under 3 weeks of trading.

---

## Cost Scaling

At higher trade volumes, only Step Functions Express increases meaningfully:

| Trades/day | Step Functions Express | Total change |
|---|---|---|
| 20 (baseline) | $0.80 | — |
| 50 | $2.00 | +$1.20 |
| 100 | $4.00 | +$3.20 |
| 200 | $8.00 | +$7.20 |

Even at 200 trades/day, total cost is ~$30/month — still 70% cheaper than NinjaTrader.
