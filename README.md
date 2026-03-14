# trade-engine

> **Why this exists**: NinjaTrader charges ~$100/month for live trade execution. This project eliminates that fee entirely by routing signals from a free NinjaTrader instance through a self-hosted cloud execution layer connecting directly to Interactive Brokers - your broker, your infrastructure, your cost.

---

## What This Does

NinjaTrader is kept purely as a **signal generator** - the part it does for free. Every buy/sell signal fires an HTTPS call to AWS, where a Step Functions state machine owns the full trade lifecycle: risk checks, order placement, fill monitoring, stop management, and position closing. A live dashboard lets you watch everything in real time from a browser.

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
├── .gitignore
├── architecture/                     ← architectural diagrams and docs
│   ├── business-architecture.md
│   ├── infrastructure-architecture.html
│   ├── application-architecture.html
│   ├── data-architecture.md
│   └── cost-analysis.md
├── dashboard-ui/                     ← S3 static site
│   ├── index.html
│   ├── login.html
│   └── js/
│       ├── config.js                 <- update: your AWS endpoints here (post-deploy)
│       ├── auth.js
│       ├── api.js
│       └── websocket.js
├── dashboard-api/                    ← FastAPI on ECS Fargate
│   ├── main.py
│   ├── routes/
│   ├── websocket/live.py
│   ├── auth/cognito.py
│   ├── Dockerfile
│   └── requirements.txt
├── lambdas/
│   ├── layer/                        ← shared Python deps (httpx, boto3)
│   │   └── requirements.txt
│   ├── api_authorizer/               ← validates X-API-Key + source IP
│   ├── signal/                       ← receives NinjaTrader signals
│   ├── risk_check/                   ← pre-trade validation
│   ├── place_order/                  ← sends order to CP Gateway
│   ├── wait_for_fill/                ← polls for fill confirmation
│   ├── set_stop/                     ← places protective stop
│   ├── check_price/                  ← Express WF price polling
│   ├── close_position/               ← market close
│   ├── log_trade/                    ← DynamoDB + SNS
│   ├── tickle/                       ← CP Gateway keep-alive
│   ├── portfolio_risk/               ← cross-trade monitor
│   └── dead_man/                     ← emergency closer
├── stepfunctions/
│   ├── trade_lifecycle.asl.json      ← Standard Workflow
│   └── monitoring_loop.asl.json      ← Express Workflow (nested)
├── gateway/
│   └── config/
│       └── ibgateway.env.example     <- copy to ibgateway.env for local dev
├── ninjatrader/
│   ├── OrchestratorClient.cs         <- update: BaseUrl + ApiKey (post-deploy)
│   └── MesConsolidationProfitHunter.cs
└── infra/                            ← AWS SAM infrastructure-as-code
    ├── template.yaml                 ← root SAM template (Lambdas, Step Functions, API GW)
    ├── samconfig.toml                ← SAM CLI configuration
    ├── docker-compose.yml            ← LOCAL DEV ONLY (IB Gateway + dashboard API)
    ├── nginx.conf                    ← local nginx for TLS termination
    └── stacks/                       ← nested CloudFormation stacks
        ├── vpc.yaml                  ← VPC, subnets, NAT, security groups, VPC endpoints
        ├── storage.yaml              ← DynamoDB, SNS, SQS, EventBridge, S3
        ├── secrets.yaml              ← Secrets Manager (IB creds, API key, Google OAuth)
        ├── cognito.yaml              ← Cognito user pool + Google federation
        ├── compute.yaml              ← EC2 (CP Gateway), ECR, ECS Fargate, ALB
        └── frontend.yaml             ← CloudFront distribution
```

---

## Quick Start

```bash
git clone https://github.com/bhaviksapru/trade-engine
cd trade-engine/infra

# Build all Lambda functions
sam build

# Deploy - fill in your actual values
sam deploy \
  --stack-name trade-engine \
  --region us-east-2 \
  --capabilities CAPABILITY_IAM CAPABILITY_AUTO_EXPAND CAPABILITY_NAMED_IAM \
  --resolve-s3 \
  --parameter-overrides \
    IbUsername=YOUR_IB_USERNAME \
    IbPassword=YOUR_IB_PASSWORD \
    IbAccountId=YOUR_IB_ACCOUNT_ID \
    IbMode=paper \
    GoogleOAuthClientId="XXXX.apps.googleusercontent.com" \
    GoogleOAuthClientSecret="XXXX" \
    AllowedGoogleEmail="your.email@gmail.com" \
    YourDesktopIp="YOUR.PUBLIC.IP" \
    AlertPhoneNumber="+1XXXXXXXXXX"
```

Then follow **SETUP.md** for the complete post-deploy configuration.

---

## Security Architecture

### TLS - HTTPS Without a Custom Domain

| Endpoint | URL Format | TLS |
|---|---|---|
| Dashboard | `https://XXXX.cloudfront.net` | CloudFront built-in, auto-renews |
| Signal / API | `https://XXXX.execute-api.REGION.amazonaws.com/prod` | API Gateway built-in, auto-renews |

All Lambda-to-EC2 traffic stays inside the private VPC subnet - never touches the public internet.

### Dashboard Authentication - Cognito + Google

Only one specific Google account can log in. Everyone else gets 403.

### NinjaTrader Signal Authentication - Lambda Authorizer

Every signal request is validated by a Lambda authorizer that checks:
- `X-API-Key` header (stored in Secrets Manager, auto-generated on deploy)
- Source IP must match `YourDesktopIp` parameter

### IAM - Least Privilege

Each Lambda has only the permissions it needs for its specific task. No Lambda has `AdministratorAccess`. All secrets are in Secrets Manager - never in environment variables or code.

---

## Endpoints After Deployment

```bash
aws cloudformation describe-stacks \
  --stack-name trade-engine \
  --query 'Stacks[0].Outputs' \
  --output table
```

```
DashboardUrl              https://XXXX.cloudfront.net
ApiGatewayUrl             https://XXXX.execute-api.us-east-2.amazonaws.com/prod
SignalEndpoint            https://XXXX.execute-api.us-east-2.amazonaws.com/prod/signal
CognitoHostedUi           https://trade-engine-XXXX.auth.us-east-2.amazoncognito.com/login
CognitoCallbackUrl        https://trade-engine-XXXX.auth.us-east-2.amazoncognito.com/oauth2/idpresponse
CognitoClientId           XXXX
EcrRepositoryUrl          XXXX.dkr.ecr.us-east-2.amazonaws.com/trade-engine-dashboard-trade-engine
Ec2InstanceId             i-XXXX
```

---

## Cost at 20 Trades/Day (Market Hours Only)

| Service | Monthly |
|---|---|
| EC2 t3.small on-demand (157.5hrs/month) | $3.28 |
| ECS Fargate 0.25vCPU/0.5GB (157.5hrs) | $1.94 |
| Step Functions Express | $0.80 |
| Step Functions Standard | $0.00 (free tier) |
| ALB | $6.26 |
| API Gateway HTTP API | $0.02 |
| All Lambdas | $0.00 (free tier) |
| DynamoDB | $0.04 |
| EventBridge | $0.00 (free tier) |
| Cognito User Pool | $0.00 (free tier — 1 MAU vs 50,000 free) |
| CloudWatch Logs | $1.06 |
| Secrets Manager | $1.60 |
| S3 + CloudFront | $0.02 |
| SNS | $0.00 |
| NAT Gateway | $7.14 |
| **Total** | **~$30/month (3-AZ HA)** |

**vs NinjaTrader execution license: ~$100/month → saving ~$64/month**

---

## Maintenance

### Redeploy after Lambda/config changes
```bash
cd infra && sam build && sam deploy --stack-name trade-engine \
  --region us-east-2 --no-confirm-changeset \
  --capabilities CAPABILITY_IAM CAPABILITY_AUTO_EXPAND CAPABILITY_NAMED_IAM \
  --resolve-s3 --parameter-overrides [... same parameters ...]
```

### Rotate API Key (every 90 days)
```bash
NEW_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
aws secretsmanager update-secret \
  --secret-id trade-engine/api-key-trade-engine \
  --secret-string "{\"api_key\":\"$NEW_KEY\"}"
# Then update OrchestratorClient.cs and recompile in NinjaTrader
```

### Destroy everything
```bash
BUCKET=$(aws cloudformation describe-stacks --stack-name trade-engine \
  --query "Stacks[0].Outputs[?OutputKey=='S3BucketName'].OutputValue" --output text)
aws s3 rm s3://$BUCKET --recursive
sam delete --stack-name trade-engine --region us-east-2
```

> DynamoDB tables have `DeletionPolicy: Retain` - your trade history is preserved.
> Delete them manually in the AWS Console if you want a clean teardown.
