# trade-engine

> **Why this exists**: NinjaTrader charges ~$100/month for live trade execution. This project eliminates that fee entirely by routing signals from a free NinjaTrader instance through a self-hosted cloud execution layer connecting directly to Interactive Brokers — your broker, your infrastructure, your cost.

---

## What This Does

NinjaTrader is kept purely as a **signal generator** — the part it does for free. Every buy/sell signal fires an HTTPS call to AWS, where a Step Functions state machine owns the full trade lifecycle: risk checks, order placement, fill monitoring, stop management, and position closing. A live dashboard lets you watch everything in real time from a browser.

```
NinjaTrader (free tier, signal detection only)
    → AWS (trade execution, risk management, monitoring)
        → Interactive Brokers (your broker, unchanged)
```

---

## Repository Structure

```
trade-engine/
├── README.md
├── SETUP.md                          ← full installation guide
├── architecture/                     ← architectural artifacts
│   ├── business-architecture.md
│   ├── infrastructure-architecture.html
│   ├── application-architecture.html
│   ├── data-architecture.md
│   └── cost-analysis.md
├── dashboard-ui/                     ← S3 static site
│   ├── index.html                    ← main dashboard
│   ├── login.html                    ← Google login page
│   └── js/
│       ├── config.js                 ← ✏️ UPDATE: your AWS endpoints here
│       ├── auth.js                   ← Cognito + Google OAuth
│       ├── api.js                    ← REST calls to FastAPI
│       └── websocket.js              ← live WebSocket connection
├── dashboard-api/                    ← FastAPI on ECS Fargate
│   ├── main.py
│   ├── routes/
│   │   ├── positions.py
│   │   ├── health.py
│   │   └── actions.py               ← POST: close, pause, notifications
│   ├── websocket/live.py
│   ├── auth/cognito.py              ← JWT verification
│   ├── Dockerfile
│   └── requirements.txt
├── lambdas/
│   ├── signal/                      ← receives NinjaTrader signals
│   ├── risk_check/                  ← pre-trade validation
│   ├── place_order/                 ← sends order to CP Gateway
│   ├── wait_for_fill/               ← polls for fill confirmation
│   ├── set_stop/                    ← places protective stop
│   ├── check_price/                 ← Express WF price polling
│   ├── close_position/              ← market close
│   ├── log_trade/                   ← DynamoDB + SNS
│   ├── tickle/                      ← CP Gateway keep-alive
│   ├── portfolio_risk/              ← cross-trade monitor
│   └── dead_man/                    ← emergency closer
├── stepfunctions/
│   ├── trade_lifecycle.asl.json     ← Standard Workflow
│   └── monitoring_loop.asl.json     ← Express Workflow (nested)
├── ninjatrader/
│   ├── OrchestratorClient.cs        ← ✏️ UPDATE: BaseUrl + ApiKey
│   └── StrategyTemplate.cs
└── infra/                           ← Terraform IaC
    ├── terraform.tfvars.example     ← ✏️ UPDATE: your values
    └── *.tf
```

---

## Security Architecture

### TLS — HTTPS Without a Custom Domain

Every public endpoint uses **AWS-managed TLS**. No certificates to buy, request, or renew manually.

| Endpoint | URL Format | TLS |
|---|---|---|
| Dashboard | `https://XXXX.cloudfront.net` | CloudFront built-in, auto-renews |
| FastAPI / Signal | `https://XXXX.execute-api.REGION.amazonaws.com/prod` | API Gateway built-in, auto-renews |

Traffic between Lambdas and EC2 (CP Gateway) stays inside your private VPC subnet — never touches the public internet, no TLS needed for that hop.

```
Internet
  ↓  HTTPS (AWS-managed TLS terminates here)
API Gateway / CloudFront
  ↓  Private VPC (unencrypted, internal AWS network only)
EC2 CP Gateway / ECS Fargate / Lambdas
```

**After `terraform apply`, copy the output URLs into:**
- `dashboard-ui/js/config.js` — lines 2–4
- `ninjatrader/OrchestratorClient.cs` — line 15

---

### Dashboard Authentication — Cognito + Google

Only one specific Google account can log in. Everyone else gets access denied.

```
User visits https://XXXX.cloudfront.net
  ↓ no valid JWT
Redirect to Cognito Hosted UI
  ↓ click "Sign in with Google"
Google OAuth flow
  ↓ returns to Cognito
Cognito Pre-Token Lambda checks:
  email == allowed_google_email?
  ├── YES → issue JWT → back to dashboard
  └── NO  → reject (403)
  ↓
Dashboard attaches JWT to every API call:
  Authorization: Bearer <id_token>
  ↓
FastAPI verifies JWT against Cognito JWKS endpoint
  ├── valid + correct email → proceed
  └── invalid → 401
```

#### Setup: Google OAuth App (one-time, ~5 min)

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. New project → APIs & Services → Credentials
3. Create OAuth 2.0 Client ID → Web application
4. Leave redirect URIs blank for now (add after Terraform runs)
5. Copy **Client ID** and **Client Secret** into `terraform.tfvars`:

```hcl
google_oauth_client_id     = "XXXX.apps.googleusercontent.com"
google_oauth_client_secret = "XXXX"
allowed_google_email       = "your.email@gmail.com"
```

#### After Terraform — Add Cognito Callback to Google

```bash
terraform output cognito_callback_url
# → https://trade-engine-XXXX.auth.REGION.amazoncognito.com/oauth2/idpresponse
```

Add this URL to your Google OAuth app under **Authorised redirect URIs**.

---

### NinjaTrader Signal Authentication — API Gateway API Key + IP Restriction

```
NinjaTrader POST .../prod/signal
  Header: X-API-Key: your-secret-key    ← API Gateway validates
  Body:   { symbol, side, quantity... }
```

API Gateway resource policy also restricts to your desktop's IP:

```json
{
  "Effect": "Deny",
  "Principal": "*",
  "Action": "execute-api:Invoke",
  "Resource": "arn:aws:execute-api:...",
  "Condition": {
    "NotIpAddress": { "aws:SourceIp": "YOUR_DESKTOP_IP/32" }
  }
}
```

**To update your API key:**
1. AWS Console → API Gateway → API Keys → your key → show
2. Update `ninjatrader/OrchestratorClient.cs` line 15
3. Recompile in NinjaTrader

---

### Security Groups

#### EC2 — CP Gateway (private subnet, never public)

```
sg-cpgateway

INBOUND:
  5000  TCP  sg-lambda-workers  (Lambdas only)
  22    TCP  YOUR_IP/32         (SSH — or disable and use SSM Session Manager)

OUTBOUND:
  443   TCP  0.0.0.0/0          (IBKR servers)
```

To update your IP: edit `terraform.tfvars` → `your_desktop_ip` → `terraform apply`

#### Lambda Workers

```
sg-lambda-workers

INBOUND:  (none — Lambdas don't accept connections)

OUTBOUND:
  5000  TCP  sg-cpgateway       (CP Gateway)
  443   TCP  vpc-endpoint-sg    (DynamoDB, Step Functions, Secrets Manager)
```

#### ECS Fargate — FastAPI

```
sg-fargate-dashboard

INBOUND:
  8000  TCP  sg-alb             (ALB only)

OUTBOUND:
  443   TCP  vpc-endpoint-sg    (DynamoDB, EventBridge)
  443   TCP  0.0.0.0/0          (Cognito JWKS validation)
```

#### ALB

```
sg-alb

INBOUND:
  443   TCP  0.0.0.0/0          (public dashboard access)
  80    TCP  0.0.0.0/0          (redirects to 443)

OUTBOUND:
  8000  TCP  sg-fargate-dashboard
```

#### VPC Endpoints (avoids ~$32/month NAT Gateway)

Terraform creates these so your private resources reach AWS services without NAT:

```
dynamodb           Gateway endpoint  (free)
states             Interface endpoint
secretsmanager     Interface endpoint
ecr.api            Interface endpoint
ecr.dkr            Interface endpoint
logs               Interface endpoint
```

Only IBKR-bound traffic uses NAT (~$2/month at this volume).

---

### IAM Roles — Least Privilege

| Lambda | Permissions |
|---|---|
| signal | `states:StartExecution` on trade-lifecycle ARN only |
| risk_check | `dynamodb:GetItem` on config table only |
| place_order | `dynamodb:PutItem` on trades table, `secretsmanager:GetSecretValue` on IB creds secret |
| check_price | `events:PutEvents` on trade-events bus |
| close_position | `dynamodb:UpdateItem`, `sns:Publish` |
| log_trade | `dynamodb:PutItem`, `sns:Publish` |
| tickle | `secretsmanager:GetSecretValue` on IB creds |
| portfolio_risk | `dynamodb:Scan`, `states:StopExecution`, `sns:Publish` |
| dead_man | `dynamodb:Scan`, `states:StopExecution`, `sns:Publish` |
| Fargate task | `dynamodb:Query`, `dynamodb:Scan` (read-only), `events:PutEvents` |

No role has `AdministratorAccess`. No role can access resources outside its scope.

---

## Endpoints After Deployment

```bash
cd infra && terraform output
```

```
dashboard_url          = "https://XXXX.cloudfront.net"
api_gateway_url        = "https://XXXX.execute-api.us-east-2.amazonaws.com/prod"
signal_endpoint        = "https://XXXX.execute-api.us-east-2.amazonaws.com/prod/signal"
cognito_hosted_ui      = "https://trade-engine-XXXX.auth.us-east-2.amazoncognito.com/login"
cognito_callback_url   = "https://trade-engine-XXXX.auth.us-east-2.amazoncognito.com/oauth2/idpresponse"
ecr_repository_url     = "XXXX.dkr.ecr.us-east-2.amazonaws.com/trade-engine-dashboard"
```

---

## Cost at 20 Trades/Day (Market Hours Only)

| Service | Monthly |
|---|---|
| EC2 t3.small on-demand (157.5hrs/month) | $3.28 |
| ECS Fargate 0.25vCPU/0.5GB (157.5hrs) | $1.94 |
| Step Functions Express | $6.00 |
| Step Functions Standard | $0.12 |
| ALB | $6.26 |
| API Gateway | $0.02 |
| All Lambdas | $1.00 |
| DynamoDB | $0.31 |
| EventBridge | $1.00 |
| CloudWatch Logs | $1.00 |
| Secrets Manager | $1.60 |
| S3 + CloudFront | $0.12 |
| SNS | $0.00 |
| NAT Gateway (minimal) | $2.00 |
| **Total** | **~$24/month** |

**vs NinjaTrader execution license: ~$100/month → saving ~$76/month**

---

## Quick Start

```bash
git clone https://github.com/bhaviksapru/trade-engine
cd trade-engine/infra
cp terraform.tfvars.example terraform.tfvars
# edit terraform.tfvars with your values
terraform init
terraform apply
```

Then follow **SETUP.md** for the complete post-deploy configuration.
