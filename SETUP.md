# SETUP.md — Complete Installation Guide

This guide walks through every step from a fresh AWS account to a running trading system. Follow in order.

---

## Prerequisites Checklist

```
□ AWS account with billing enabled
□ AWS CLI installed locally  (brew install awscli  or  https://aws.amazon.com/cli)
□ Terraform >= 1.5 installed (brew install terraform or https://terraform.io)
□ Docker installed locally   (https://docker.com)
□ NinjaTrader 8 installed on your desktop (free tier is fine)
□ IBKR account (paper trading account works for testing)
□ Google account (for dashboard login)
```

---

## Step 1 — AWS CLI Setup

```bash
aws configure
# AWS Access Key ID:     <your access key>
# AWS Secret Access Key: <your secret key>
# Default region:        us-east-2
# Default output format: json
```

Create a dedicated IAM user for Terraform (recommended — don't use root):
```
AWS Console → IAM → Users → Create user
  Username: terraform-trade-engine
  Permissions: AdministratorAccess (for initial deploy only)
  → Create access key → Command Line Interface
  → Copy keys into aws configure above
```

---

## Step 2 — Google OAuth App

This takes ~5 minutes and must be done before Terraform.

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Top bar → Select project → New Project
   - Name: `trade-engine-dashboard`
3. APIs & Services → OAuth consent screen
   - User Type: **External**
   - App name: `Trade Engine Dashboard`
   - User support email: your Gmail
   - Developer contact: your Gmail
   - Save and Continue (skip scopes, skip test users)
4. APIs & Services → Credentials → Create Credentials → OAuth 2.0 Client ID
   - Application type: **Web application**
   - Name: `trade-engine-web`
   - Authorised JavaScript origins: leave blank for now
   - Authorised redirect URIs: leave blank for now
   - Click Create
5. Copy the **Client ID** and **Client Secret** — you'll need them in Step 3

---

## Step 3 — Configure Terraform Variables

```bash
cd infra
cp terraform.tfvars.example terraform.tfvars
```

Open `terraform.tfvars` and fill in every value:

```hcl
# ── AWS ──────────────────────────────────────────────────────
aws_region = "us-east-2"           # your preferred AWS region

# ── IB Credentials ───────────────────────────────────────────
# These go into Secrets Manager — never committed to git
ib_username   = "YOUR_IB_USERNAME"
ib_password   = "YOUR_IB_PASSWORD"
ib_account_id = "YOUR_IB_ACCOUNT_ID"
ib_mode       = "paper"            # "paper" or "live"
                                   # ⚠️  start with "paper" until fully tested

# ── Google OAuth (from Step 2) ────────────────────────────────
google_oauth_client_id     = "XXXX.apps.googleusercontent.com"
google_oauth_client_secret = "XXXX"
allowed_google_email       = "your.actual.email@gmail.com"
                             # ONLY this Google account can log into dashboard

# ── Your Desktop (NinjaTrader machine) ───────────────────────
# Find your public IP: https://whatismyip.com
your_desktop_ip = "YOUR.PUBLIC.IP.HERE"   # e.g. "12.34.56.78"

# ── Alerts ───────────────────────────────────────────────────
alert_phone_number = "+1XXXXXXXXXX"       # SNS SMS alerts (include country code)

# ── Risk Defaults (adjustable later from dashboard) ──────────
max_position_size   = 10     # max contracts/shares per strategy
max_daily_loss_usd  = 500    # daily loss limit in USD
order_cooldown_secs = 5      # seconds between identical signals
```

---

## Step 4 — Initialize and Deploy Infrastructure

```bash
cd infra
terraform init
```

Review the plan before applying (important — read through it):
```bash
terraform plan
```

Deploy everything (~8 minutes):
```bash
terraform apply
# Type "yes" when prompted
```

When complete, save all outputs:
```bash
terraform output > ../deployment-outputs.txt
cat ../deployment-outputs.txt
```

You will see something like:
```
dashboard_url         = "https://d1abc123def456.cloudfront.net"
api_gateway_url       = "https://abc12345.execute-api.us-east-2.amazonaws.com/prod"
signal_endpoint       = "https://abc12345.execute-api.us-east-2.amazonaws.com/prod/signal"
cognito_hosted_ui     = "https://trade-engine-abc123.auth.us-east-2.amazoncognito.com/login"
cognito_callback_url  = "https://trade-engine-abc123.auth.us-east-2.amazoncognito.com/oauth2/idpresponse"
cognito_client_id     = "1abc2def3ghi4jkl"
ecr_repository_url    = "123456789.dkr.ecr.us-east-2.amazonaws.com/trade-engine-dashboard"
ec2_instance_id       = "i-0abc123def456789"
```

Keep this file — you need these values in the next steps.

---

## Step 5 — Add Google Callback URL

Now that Terraform has created your Cognito domain, go back to Google Console:

1. [console.cloud.google.com](https://console.cloud.google.com) → APIs & Services → Credentials
2. Click your OAuth 2.0 Client ID (`trade-engine-web`)
3. Under **Authorised redirect URIs** → Add URI:
   ```
   (paste your cognito_callback_url from deployment-outputs.txt)
   ```
   Example: `https://trade-engine-abc123.auth.us-east-2.amazoncognito.com/oauth2/idpresponse`
4. Under **Authorised JavaScript origins** → Add URI:
   ```
   (paste your dashboard_url from deployment-outputs.txt)
   ```
   Example: `https://d1abc123def456.cloudfront.net`
5. Click Save

---

## Step 6 — Build and Push Docker Image

```bash
cd dashboard-api

# Authenticate Docker to ECR
aws ecr get-login-password --region us-east-2 | \
  docker login --username AWS --password-stdin \
  $(terraform -chdir=../infra output -raw ecr_repository_url | cut -d/ -f1)

# Build image
docker build -t trade-engine-dashboard .

# Tag and push
ECR_URL=$(cd ../infra && terraform output -raw ecr_repository_url)
docker tag trade-engine-dashboard:latest $ECR_URL:latest
docker push $ECR_URL:latest
```

Force ECS to pull the new image:
```bash
aws ecs update-service \
  --cluster trade-engine \
  --service dashboard-api \
  --force-new-deployment \
  --region us-east-2
```

---

## Step 7 — Configure Dashboard UI

Update the config file with your actual endpoints:

```bash
# Open dashboard-ui/js/config.js and update these lines:
```

```javascript
// dashboard-ui/js/config.js — UPDATE THESE VALUES
window.CONFIG = {
  apiUrl:          "PASTE_api_gateway_url_HERE",
  cognitoDomain:   "PASTE_cognito_hosted_ui_domain_HERE",
  // e.g. "https://trade-engine-abc123.auth.us-east-2.amazoncognito.com"
  cognitoClientId: "PASTE_cognito_client_id_HERE",
  dashboardUrl:    "PASTE_dashboard_url_HERE",
};
```

Deploy to S3:
```bash
BUCKET=$(cd infra && terraform output -raw s3_bucket_name)

aws s3 sync dashboard-ui/ s3://$BUCKET/ \
  --delete \
  --cache-control "max-age=300"

# Invalidate CloudFront cache so changes appear immediately
DIST_ID=$(cd infra && terraform output -raw cloudfront_distribution_id)
aws cloudfront create-invalidation \
  --distribution-id $DIST_ID \
  --paths "/*"
```

---

## Step 8 — Configure NinjaTrader

1. Find your API key:
   ```bash
   aws secretsmanager get-secret-value \
     --secret-id trade-engine/api-key \
     --query SecretString \
     --output text | python3 -c "import sys,json; print(json.load(sys.stdin)['api_key'])"
   ```

2. Open `ninjatrader/OrchestratorClient.cs` and update lines 14–15:
   ```csharp
   private const string BaseUrl = "PASTE_signal_endpoint_base_HERE";
   // e.g. "https://abc12345.execute-api.us-east-2.amazonaws.com/prod"
   private const string ApiKey  = "PASTE_API_KEY_HERE";
   ```

3. Copy both files to NinjaTrader:
   - Windows: `Documents\NinjaTrader 8\bin\Custom\`
   - Copy `OrchestratorClient.cs`
   - Copy `StrategyTemplate.cs` (or your own strategy file)

4. In NinjaTrader:
   - Tools → Edit NinjaScript → Select file → Compile
   - Do this for both files
   - Green checkmark = success

---

## Step 9 — Verify the System

Run these checks in order on a weekday between 9:00am–4:00pm ET.

**Check 1: EC2 started**
```bash
aws ec2 describe-instances \
  --instance-ids $(cd infra && terraform output -raw ec2_instance_id) \
  --query 'Reservations[0].Instances[0].State.Name' \
  --output text
# Expected: "running"
```

**Check 2: CP Gateway authenticated**
```bash
# The health endpoint proxies to CP Gateway via FastAPI
curl -H "Authorization: Bearer $(get_cognito_token)" \
  $(cd infra && terraform output -raw api_gateway_url)/health
# Expected: { "cp_gateway": "authenticated", "trading_enabled": true }
```

**Check 3: Dashboard loads**
```
Open: (your dashboard_url from deployment-outputs.txt)
→ Should redirect to Google login
→ Sign in with your allowed Google account
→ Should see dashboard with empty positions
```

**Check 4: Test signal (paper trading only)**
```bash
API_KEY=$(aws secretsmanager get-secret-value --secret-id trade-engine/api-key \
  --query SecretString --output text | python3 -c "import sys,json; print(json.load(sys.stdin)['api_key'])")

curl -X POST \
  $(cd infra && terraform output -raw signal_endpoint) \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"strategy_id":"test","symbol":"AAPL","side":"BUY","quantity":1,"order_type":"MKT"}'

# Expected: { "status": "accepted", "execution_arn": "arn:aws:states:..." }
```

**Check 5: Watch it in Step Functions**
```
AWS Console → Step Functions → State machines → trade-lifecycle
→ Executions → should see your test execution running
→ Click it → watch the state transitions in real time
```

---

## Step 10 — Switch to Live Trading

Only after paper trading works correctly for at least 1 week:

1. Update `terraform.tfvars`:
   ```hcl
   ib_mode = "live"
   ```

2. Update the secret:
   ```bash
   aws secretsmanager update-secret \
     --secret-id trade-engine/ib-credentials \
     --secret-string '{"username":"YOUR_IB_USERNAME","password":"YOUR_IB_PASSWORD","account_id":"YOUR_IB_ACCOUNT_ID","mode":"live"}'
   ```

3. Restart CP Gateway to pick up the new mode:
   ```bash
   EC2_ID=$(cd infra && terraform output -raw ec2_instance_id)
   aws ec2 reboot-instances --instance-ids $EC2_ID
   # Wait 3 minutes for CP Gateway to re-authenticate
   curl .../health  # verify authenticated
   ```

---

## Maintenance

### Daily (automatic — no action needed)
- EC2 starts at 9:00am ET, stops at 4:30pm ET
- CP Gateway authenticates automatically on start
- ECS Fargate scales to 1 task at 9:00am, 0 at 4:30pm

### Weekly
```
□ Review CloudWatch Logs for Lambda errors
  AWS Console → CloudWatch → Log groups → /aws/lambda/trade-engine-*
□ Check Step Functions execution history for any FAILED executions
□ Verify dead man Lambda is running (CloudWatch → Rules → trade-engine-deadman)
```

### Monthly
```
□ Check AWS Cost Explorer — verify bill matches expected ~$24/month
□ Review DynamoDB consumed capacity — should not be spiking
□ Check CloudWatch log storage (auto-expires at 30 days if Terraform applied correctly)
```

### Every 90 Days — Rotate API Key
```bash
# 1. Generate new key
NEW_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")

# 2. Update Secrets Manager
aws secretsmanager update-secret \
  --secret-id trade-engine/api-key \
  --secret-string "{\"api_key\":\"$NEW_KEY\"}"

# 3. Update API Gateway usage plan key
# AWS Console → API Gateway → API Keys → delete old → create new → add to usage plan

# 4. Update NinjaTrader OrchestratorClient.cs line 15
# Recompile in NinjaTrader
```

### If Your Home IP Changes
```bash
# Update terraform.tfvars
your_desktop_ip = "NEW.IP.ADDRESS"

cd infra && terraform apply
# Only security group changes — takes ~30 seconds, no downtime
```

---

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---|---|---|
| Signal returns 403 | Wrong API key or IP not whitelisted | Check `OrchestratorClient.cs` key; check `terraform.tfvars` IP |
| Dashboard shows "Cannot connect" | Config.js not updated | Re-run Step 7 |
| Dashboard login loops | Google callback URL wrong | Re-check Step 5 |
| Dashboard login "Access Denied" | Wrong Google account | Only `allowed_google_email` can log in |
| CP Gateway not authenticated at market open | EC2 took too long to start | Change scheduler to 8:45am ET in `infra/scheduler.tf` |
| Step Function stuck at WAIT_FOR_FILL | CP Gateway lost IB session | Check /health; trigger reauth via dashboard |
| Fargate task not starting | ECR image not pushed | Re-run Step 6 |
| No SNS texts arriving | Phone number not confirmed | AWS Console → SNS → Subscriptions → confirm your number |
| ECS task keeps restarting | App crash — check logs | `aws logs tail /ecs/trade-engine-dashboard --follow` |
| Dead man fired unexpectedly | Heartbeat gap > 15min | Check check_price Lambda CloudWatch logs |

---

## Common Tasks

### View live logs
```bash
# FastAPI dashboard
aws logs tail /ecs/trade-engine-dashboard --follow

# Any Lambda
aws logs tail /aws/lambda/trade-engine-place-order --follow

# All trade-engine logs together
aws logs tail /aws/lambda/trade-engine --follow --log-group-pattern trade-engine
```

### Manually close all positions (emergency)
```
Dashboard → top right → "Close All" button
OR
curl -X POST .../actions/close-all-positions -H "Authorization: Bearer <token>"
```

### Pause trading without stopping everything
```
Dashboard → toggle "Trading Enabled"
OR
aws dynamodb put-item --table-name trade-engine-config \
  --item '{"pk":{"S":"trading_enabled"},"value":{"BOOL":false}}'
```

### SSH into EC2 (for CP Gateway diagnostics)
```bash
# Preferred: SSM Session Manager (no port 22 needed)
aws ssm start-session --target $(cd infra && terraform output -raw ec2_instance_id)

# Inside the session:
docker logs cp-gateway --tail 100
docker ps
```

### Destroy everything
```bash
cd infra
terraform destroy
# Type "yes"
# Note: S3 bucket must be emptied first:
aws s3 rm s3://$(terraform output -raw s3_bucket_name) --recursive
```
