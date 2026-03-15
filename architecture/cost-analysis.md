# Cost Analysis

## Assumptions

- **Trading days per month:** 21 (US market average)
- **Trading hours:** 9:00am - 4:30pm ET = 7.5hrs/day with buffer
- **Compute hours/month:** 21 x 7.5 = 157.5 hours
- **Trades per day:** 20
- **Trades per month:** 420
- **AWS Region:** us-east-2 (Ohio) - generally cheapest US region
- **Pricing:** on-demand, as of 2024 (check aws.amazon.com/pricing for current rates)

---

## Compute

### EC2 t3.small (CP Gateway)

The CP Gateway is a Java process requiring ~1GB RAM minimum. t3.small has 2GB RAM and 2 vCPUs.

```
On-demand rate:       $0.0208/hour
Hours/month:          157.5
Monthly cost:         $0.0208 x 157.5 = $3.28

vs always-on:         $0.0208 x 720 = $14.98/month
Saving from schedule: $11.70/month
```

### ECS Fargate - FastAPI Dashboard

0.25 vCPU, 0.5 GB memory. Runs on FARGATE_SPOT with FARGATE as fallback.

```
FARGATE_SPOT rate (vCPU):    ~$0.01219/vCPU-hour  (approx 70% off on-demand)
FARGATE_SPOT rate (memory):  ~$0.00134/GB-hour

vCPU cost:   $0.01219 x 0.25 x 157.5 = $0.48
Memory cost: $0.00134 x 0.5  x 157.5 = $0.11
Monthly:     $0.59

vs on-demand Fargate:  $1.94/month
Saving from Spot:      $1.35/month

Note: Spot capacity is almost always available for small 0.25 vCPU tasks.
On the rare occasion no Spot is available, the service falls back to one
on-demand task (Base=1 in the capacity provider strategy), so the dashboard
never goes dark. On-demand fallback cost is $1.94/month worst case.
```

---

## Step Functions

### Standard Workflow (outer trade lifecycle)

~8 state transitions per trade:
PENDING -> RISK_CHECK -> PLACE_ORDER -> WAIT_FILL -> SET_STOP -> MONITORING -> CLOSE -> LOG

```
Transitions/month:  420 trades x 8 = 3,360
Free tier:          4,000/month
Monthly cost:       $0.00  (within free tier)

Note: first 4,000 transitions/month are always free
```

### Express Workflow (nested monitoring loop)

Price checked every 5 seconds. 2 transitions per check (CHECK_PRICE + EVALUATE).
Average trade duration: 30 minutes = 360 checks x 2 = 720 transitions/trade.

```
Transitions/month:  420 x 720 = 302,400
Rate:               $1.00/million transitions
Transition cost:    $0.30

Duration cost:      420 trades x 30min x 64MB = 806,400 GB-seconds
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

## Application Load Balancer (Dashboard)

The ALB is permanently deployed in the 3 public subnets and fronts the ECS
Fargate dashboard service. CloudFront routes API and WebSocket traffic through
it. The ALB stays up 24/7; only the Fargate tasks behind it are scaled to 0
outside market hours.

```
Hourly rate:       $0.008/hour
Hours/month:       720 (always-on)
Fixed cost:        $0.008 x 720 = $5.76

LCU charges:       ~$0.50 (minimal traffic)
Monthly total:     ~$6.26
```

---

## Internal Network Load Balancer (CP Gateway)

The internal NLB sits in the 3 private subnets and provides a stable DNS name
for the CP Gateway ASG. When the ASG replaces the EC2 instance across AZs,
the NLB DNS name stays constant so Lambdas and Fargate tasks never need
reconfiguring. The NLB stays up 24/7; there are simply 0 healthy targets
registered outside market hours.

```
Hourly rate:       $0.008/hour
Hours/month:       720 (always-on)
Fixed cost:        $0.008 x 720 = $5.76

LCU charges:       ~$0.05 (low-volume TCP traffic)
Monthly total:     ~$5.81
```

---

## Lambda Functions

11 core Lambda functions. Most run only during market hours (157.5hrs/month).

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
| tickle | 8,505 (every 55s x 157.5hrs) | 200ms | 128MB |
| portfolio_risk | 9,450 (every 60s x 157.5hrs) | 300ms | 128MB |
| dead_man | 8,748 (every 5min x 720hrs - runs 24/7) | 300ms | 128MB |

```
Total GB-seconds: ~85,100
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
  420 trades x 50 = 21,000 writes
  Config updates: ~100/month
  Total writes: ~21,100/month
  Rate: $1.25/million WCU = $0.03

Reads:
  Dashboard queries: ~500/day x 21 = 10,500
  Lambda reads: ~5,000/month
  Total reads: ~15,500/month
  Rate: $0.25/million RCU = $0.004

Storage:
  Trades table: ~420 records/month x ~2KB = ~840KB/month growth
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
  Trade events:  420 x 5 = 2,100
  Total: ~304,500

Free tier: 1,000,000 events/month (custom buses)
Monthly cost: $0.00
```

---

## CloudWatch Logs

Log retention set to 30 days in CloudFormation.

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
4 x $0.40 = $1.60

API calls: ~500/month (well within included 10,000/month per secret)

Monthly total: $1.60
```

---

## Cognito

Cognito User Pool provides dashboard authentication via Google federation.
The dashboard is single-operator: one Google account is whitelisted in
`AllowedGoogleEmail`; every other identity receives a 403 before any AWS
resource is touched.

```
Monthly Active Users (MAUs): 1  (single operator)
Free tier:                    50,000 MAUs/month — permanent, not a 12-month trial
Monthly cost:                 $0.00

Breakdown for reference:
  User Pool:          free up to 50,000 MAUs
  Google federation:  included in MAU count (no separate SAML/OIDC charge
                      because Google is a "social IdP" in Cognito pricing)
  Hosted UI:          no additional charge
  Token operations:   ~42 logins/month (2x per trading day x 21 days)
                      well within the 50,000 MAU threshold

If the operator count ever exceeds 50,000 MAUs the rate is $0.0055/MAU.
At any realistic scale for a personal trading system this remains $0.00.
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
  Data transfer: ~25MB/month (50KB x 500 loads)
  HTTPS requests: ~2,500/month
  First 1TB transfer: $0.085/GB = $0.002
  First 10M requests free
  Monthly: ~$0.01

Combined: ~$0.02
```

---

## NAT Gateway

Three NAT Gateways (one per AZ) for high-availability egress. VPC Endpoints
handle all AWS service traffic internally; only the CP Gateway's outbound
connection to IBKR flows through NAT.

```
IBKR traffic: ~50MB/day x 21 days = ~1GB/month (concentrated in AZ-a
where the EC2 instance lives)

NAT Gateway hours (3 GWs x 157.5hrs): 3 x $0.045/hr x 157.5hrs = $21.26
Data processed: 1GB x $0.045/GB = $0.05

Note: AZ-b and AZ-c NAT Gateways carry near-zero data. Their cost is
purely the hourly charge. They exist so that Lambda and Fargate egress
stays healthy if AZ-a's NAT fails mid-session.

Monthly total: $21.31
```

---

## CloudWatch Alarms

12 alarms covering Lambda errors, Step Functions failures, and ECS task count.

```
Alarms:            12
Rate:              $0.10/alarm/month (standard resolution)
Monthly cost:      $1.20

Each alarm feeds the existing SNS topic - no additional SNS cost.
```


---

## Monthly Cost Summary

| Service | Cost | Notes |
|---|---|---|
| NAT Gateway (x3) | $21.31 | One per AZ, market hours only |
| ALB (dashboard) | $6.26 | Always-on, Fargate tasks scale to 0 overnight |
| Internal NLB (CP Gateway) | $5.81 | Always-on, 0 healthy targets overnight |
| EC2 t3.small | $3.28 | Market hours only (ASG ScheduledAction) |
| ECS Fargate (Spot) | $0.59 | Market hours, ~70% off on-demand |
| Secrets Manager | $1.60 | Fixed per secret |
| CloudWatch Logs | $1.06 | 30-day retention |
| Step Functions Express | $0.80 | Monitoring loop |
| Step Functions Standard | $0.00 | Free tier |
| Lambda | $0.00 | Free tier |
| DynamoDB | $0.04 | On-demand, low volume |
| API Gateway | $0.00 | Free tier |
| EventBridge | $0.00 | Free tier |
| Cognito User Pool | $0.00 | Free tier — single operator (1 MAU vs 50,000 free) |
| S3 + CloudFront | $0.02 | Tiny static site |
| CloudWatch Alarms | $1.20 | 12 alarms, Lambda + SF + ECS |
| **Total** | **~$41.97** | |

---

## Annual Comparison

| | Monthly | Annual |
|---|---|---|
| NinjaTrader execution licence | ~$100 | ~$1,200 |
| trade-engine (AWS, 3-AZ HA) | ~$42 | ~$504 |
| **Saving** | **~$58** | **~$696** |

Break-even: infrastructure pays for itself in under 3 weeks of trading.

---

## Cost Scaling

At higher trade volumes, only Step Functions Express increases meaningfully:

| Trades/day | Step Functions Express | Total change |
|---|---|---|
| 20 (baseline) | $0.80 | - |
| 50 | $2.00 | +$1.20 |
| 100 | $4.00 | +$3.20 |
| 200 | $8.00 | +$7.20 |

Even at 200 trades/day, total cost is ~$49/month - still 51% cheaper than NinjaTrader.

---

## Cost Reduction Options

These are explicit trade-offs. The defaults above (3x NAT, always-on ALB+NLB, Fargate Spot)
are already applied. The options below go further and carry real operational costs.

### Option A: Single NAT Gateway instead of three

Swap to one NAT Gateway in AZ-a only. All three private route tables point at the
same gateway.

```
Saving:    $14.22/month ($21.31 -> $7.09)
New total: ~$27.75/month

Trade-off: If AZ-a's NAT fails, Lambda and Fargate tasks in AZ-b and AZ-c
           lose internet egress. In practice this means the tickle Lambda
           stops keeping the CP Gateway session alive, and active trades
           could be orphaned until the dead_man Lambda closes them.
           The EC2 CP Gateway itself is in AZ-a, so its traffic is unaffected.

Who this suits: Acceptable for paper trading or if you can tolerate a few
minutes of disruption during an AZ event (rare, but not impossible).
```

To apply: edit `infra/stacks/vpc.yaml`, remove `NatGateway2`, `NatGateway3`,
`NatEip2`, `NatEip3`, and point `PrivateRoute2` and `PrivateRoute3` at
`!Ref NatGateway1` instead of their respective gateways.

### Option B: NAT Instance instead of NAT Gateways

Replace each NAT Gateway with a t3.nano or t3.micro EC2 instance running
Amazon Linux 2 with IP forwarding enabled.

```
t3.nano on-demand:  $0.0052/hour
3x instances x 157.5hrs: $2.46/month

Saving vs 3x NAT GW: $18.85/month
Saving vs 1x NAT GW: $4.63/month
New total: ~$21.12/month

Trade-off: A NAT instance is a single point of failure per AZ. It requires
           maintenance (patching, instance replacement on hardware failure).
           NAT Gateway is fully managed with higher bandwidth and availability.
           For a small single-user trading system, the operational overhead
           is manageable but non-zero.

Bandwidth cap: t3.nano baseline is ~32 Mbps. IBKR market data for a single
               MES contract is well under 1 Mbps, so this is not a concern.
```

To apply: create an EC2 instance per AZ with `SourceDestCheck: false`,
a public IP, and a userdata script that sets `net.ipv4.ip_forward=1` and
runs `iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE`. Point private
route tables at the instance ID (not a gateway).

### Option C: Remove AZ-b and AZ-c entirely (single-AZ deployment)

Run everything in one AZ. One public subnet, one private subnet, one NAT.

```
Infrastructure saving: ~$14/month vs current 3-AZ design
New total: ~$27/month

Trade-off: No redundancy at all. An AZ outage takes down EC2, Fargate, and
           Lambda simultaneously. This is the right choice for development or
           if NinjaTrader and this system are on the same physical machine
           anyway (making AZ redundancy academic).
```
