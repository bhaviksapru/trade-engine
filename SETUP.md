# SETUP.md - Complete Installation Guide (AWS SAM)

This guide walks through every step from a fresh AWS account to a running trading system.
Infrastructure is managed entirely with **AWS SAM** (no Terraform required).

---

## Prerequisites Checklist

```
□ AWS account with billing enabled
□ AWS CLI installed and configured  (https://aws.amazon.com/cli)
□ AWS SAM CLI installed             (https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html)
□ Docker installed locally          (https://docker.com) - required by SAM for Lambda builds
□ NinjaTrader 8 on your desktop     (free tier is fine - signal generation only)
□ IBKR account                      (paper trading account works for testing)
□ Google account                    (for dashboard login)
```

---

## Step 1 - AWS CLI Setup

```bash
aws configure
# AWS Access Key ID:     <your access key>
# AWS Secret Access Key: <your secret key>
# Default region:        us-east-2
# Default output format: json
```

Create a dedicated IAM user for deployment (recommended - don't use root):
```
AWS Console → IAM → Users → Create user
  Username: trade-engine-deployer
  Permissions: AdministratorAccess (for initial deploy - can be scoped down later)
  → Create access key → Command Line Interface
  → aws configure with these keys
```

---

## Step 2 - Google OAuth App

This takes ~5 minutes and must be done before deploying.

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Top bar → New Project → name: `trade-engine-dashboard`
3. APIs & Services → OAuth consent screen
   - User Type: **External**
   - App name: `Trade Engine Dashboard`
   - User support + developer contact: your Gmail
   - Save and Continue (skip scopes and test users)
4. APIs & Services → Credentials → Create Credentials → OAuth 2.0 Client ID
   - Application type: **Web application**
   - Name: `trade-engine-web`
   - Leave redirect URIs **blank for now** (added in Step 6)
   - Click Create
5. Copy the **Client ID** and **Client Secret** - needed in Step 3

---

## Step 3 - Build and Deploy

```bash
cd infra
sam build
```

Deploy with your values:
```bash
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
    AlertPhoneNumber="+1XXXXXXXXXX" \
    MaxPositionSize=10 \
    MaxDailyLossUsd=500 \
    OrderCooldownSecs=5
```

> SAM will show a changeset preview and ask for confirmation. Review it, then type **y**.
> The full deploy takes approximately **10–15 minutes** (EC2 bootstrap is the slow step).

When complete, save all outputs:
```bash
aws cloudformation describe-stacks \
  --stack-name trade-engine \
  --query 'Stacks[0].Outputs' \
  --output table | tee deployment-outputs.txt
```

You will see:
```
DashboardUrl              https://d1abc123.cloudfront.net
ApiGatewayUrl             https://abc123.execute-api.us-east-2.amazonaws.com/prod
SignalEndpoint            https://abc123.execute-api.us-east-2.amazonaws.com/prod/signal
CognitoHostedUi           https://trade-engine-xxx.auth.us-east-2.amazoncognito.com/login
CognitoCallbackUrl        https://trade-engine-xxx.auth.us-east-2.amazoncognito.com/oauth2/idpresponse
CognitoClientId           1abc2def3ghi4jkl
EcrRepositoryUrl          123456789.dkr.ecr.us-east-2.amazonaws.com/trade-engine-dashboard-trade-engine
Ec2InstanceId             i-0abc123def456789
S3BucketName              trade-engine-dashboard-trade-engine-123456789
CloudFrontDistributionId  EABC123DEF
```

---

## Step 4 - Add Google Callback URLs

Now that Cognito is deployed, update your Google OAuth app:

1. [console.cloud.google.com](https://console.cloud.google.com) → APIs & Services → Credentials
2. Click your OAuth Client ID (`trade-engine-web`)
3. **Authorised redirect URIs** → Add:
   ```
   (paste CognitoCallbackUrl from deployment-outputs.txt)
   ```
4. **Authorised JavaScript origins** → Add:
   ```
   (paste DashboardUrl from deployment-outputs.txt)
   ```
5. Click Save

Then update the Cognito App Client with the real dashboard URL:
```bash
# Get the User Pool ID
USER_POOL_ID=$(aws cloudformation describe-stacks \
  --stack-name trade-engine \
  --query "Stacks[0].Outputs[?OutputKey=='CognitoHostedUi'].OutputValue" \
  --output text | sed 's|https://trade-engine-||;s|\.auth.*||')

CLIENT_ID=$(aws cloudformation describe-stacks \
  --stack-name trade-engine \
  --query "Stacks[0].Outputs[?OutputKey=='CognitoClientId'].OutputValue" \
  --output text)

DASHBOARD_URL=$(aws cloudformation describe-stacks \
  --stack-name trade-engine \
  --query "Stacks[0].Outputs[?OutputKey=='DashboardUrl'].OutputValue" \
  --output text)

aws cognito-idp update-user-pool-client \
  --user-pool-id $USER_POOL_ID \
  --client-id $CLIENT_ID \
  --callback-urls "$DASHBOARD_URL" \
  --logout-urls "$DASHBOARD_URL" \
  --supported-identity-providers Google \
  --allowed-o-auth-flows code \
  --allowed-o-auth-scopes email openid profile \
  --allowed-o-auth-flows-user-pool-client
```

---

## Step 5 - Build and Push Dashboard Docker Image

```bash
cd dashboard-api

# Get ECR URL from outputs
ECR_URL=$(aws cloudformation describe-stacks \
  --stack-name trade-engine \
  --query "Stacks[0].Outputs[?OutputKey=='EcrRepositoryUrl'].OutputValue" \
  --output text)

# Authenticate Docker to ECR
aws ecr get-login-password --region us-east-2 | \
  docker login --username AWS --password-stdin \
  $(echo $ECR_URL | cut -d/ -f1)

# Build and push
docker build -t trade-engine-dashboard .
docker tag trade-engine-dashboard:latest $ECR_URL:latest
docker push $ECR_URL:latest
```

Update the ECS task definition to pick up the new image:
```bash
CLUSTER=$(aws cloudformation describe-stacks \
  --stack-name trade-engine \
  --query "Stacks[0].Outputs[?OutputKey=='EcsClusterName'].OutputValue" \
  --output text)

aws ecs update-service \
  --cluster $CLUSTER \
  --service dashboard-api-trade-engine \
  --force-new-deployment \
  --region us-east-2
```

---

## Step 6 - Configure Dashboard UI

Update `dashboard-ui/js/config.js` with your actual output values:

```javascript
window.CONFIG = {
  apiUrl:          "PASTE_ApiGatewayUrl_HERE",
  dashboardUrl:    "PASTE_DashboardUrl_HERE",
  cognitoDomain:   "PASTE_CognitoDomain_HERE",
  // e.g. "https://trade-engine-xxx.auth.us-east-2.amazoncognito.com"
  cognitoClientId: "PASTE_CognitoClientId_HERE",
  cognitoScopes:   "email openid",
};
```

Also update the `ALLOWED_ORIGINS` environment variable in the ECS task definition:
```bash
# Re-deploy SAM with the correct dashboard URL in ALLOWED_ORIGINS
sam deploy \
  --stack-name trade-engine \
  --region us-east-2 \
  --capabilities CAPABILITY_IAM CAPABILITY_AUTO_EXPAND CAPABILITY_NAMED_IAM \
  --resolve-s3 \
  --no-confirm-changeset \
  --parameter-overrides \
    ... (same as Step 3) ...
```

Deploy dashboard UI to S3:
```bash
BUCKET=$(aws cloudformation describe-stacks \
  --stack-name trade-engine \
  --query "Stacks[0].Outputs[?OutputKey=='S3BucketName'].OutputValue" \
  --output text)

aws s3 sync dashboard-ui/ s3://$BUCKET/ \
  --delete \
  --cache-control "max-age=300"

# Invalidate CloudFront cache
DIST_ID=$(aws cloudformation describe-stacks \
  --stack-name trade-engine \
  --query "Stacks[0].Outputs[?OutputKey=='CloudFrontDistributionId'].OutputValue" \
  --output text)

aws cloudfront create-invalidation \
  --distribution-id $DIST_ID \
  --paths "/*"
```

---

## Step 7 - Configure NinjaTrader

**7a — Set the Windows environment variable for the API key**

The API key is never stored in source code. It is read at runtime from a Windows
environment variable (`TRADE_ENGINE_API_KEY`). Set it once on your desktop:

1. Win + S → search **"Edit the system environment variables"**
2. Click **Environment Variables**
3. Under **User variables** → **New**
   - Variable name: `TRADE_ENGINE_API_KEY`
   - Variable value: *(output of the command below)*
4. Click OK → **restart NinjaTrader** for the change to take effect

Retrieve the key value:
```bash
API_KEY_SECRET=$(aws cloudformation describe-stacks \
  --stack-name trade-engine \
  --query "Stacks[0].Outputs[?OutputKey=='ApiKeySecretName'].OutputValue" \
  --output text 2>/dev/null || \
  echo "trade-engine/api-key-trade-engine")

aws secretsmanager get-secret-value \
  --secret-id $API_KEY_SECRET \
  --query SecretString \
  --output text | python3 -c "import sys,json; print(json.load(sys.stdin)['api_key'])"
```

> **Never paste the key directly into `OrchestratorClient.cs` or commit it to git.**
> The env var approach means rotating the key (every 90 days) only requires updating
> the env var and restarting NinjaTrader — no code changes, no recompile.

**7b — Set the API Gateway URL in `OrchestratorClient.cs`**

Update the `BaseUrl` constant on line 21 (the only line you need to edit in the file):
```csharp
private const string BaseUrl = "PASTE_ApiGatewayUrl_HERE";
// e.g. "https://abc123.execute-api.us-east-2.amazonaws.com/prod"
```

**7c — Copy and compile**

Copy both `.cs` files to NinjaTrader:
- Windows: `Documents\NinjaTrader 8\bin\Custom\`
- Compile: NinjaTrader → Tools → Edit NinjaScript → Compile both files

---

## Step 8 - Verify the System

Run these checks between 9:30am–4:00pm ET on a weekday.

**Check 1: EC2 started**
```bash
EC2_ID=$(aws cloudformation describe-stacks \
  --stack-name trade-engine \
  --query "Stacks[0].Outputs[?OutputKey=='Ec2InstanceId'].OutputValue" \
  --output text)

aws ec2 describe-instances \
  --instance-ids $EC2_ID \
  --query 'Reservations[0].Instances[0].State.Name' \
  --output text
# Expected: running
```

**Check 2: ECS task running**
```bash
aws ecs describe-services \
  --cluster trade-engine-trade-engine \
  --services dashboard-api-trade-engine \
  --query 'services[0].runningCount' \
  --output text
# Expected: 1
```

**Check 3: Dashboard loads**
```
Open: (DashboardUrl from deployment-outputs.txt)
→ Redirects to Google login → sign in with AllowedGoogleEmail
→ Should see dashboard with no open positions
```

**Check 4: Send test signal**
```bash
SIGNAL_URL=$(aws cloudformation describe-stacks \
  --stack-name trade-engine \
  --query "Stacks[0].Outputs[?OutputKey=='SignalEndpoint'].OutputValue" \
  --output text)

API_KEY=$(aws secretsmanager get-secret-value \
  --secret-id trade-engine/api-key-trade-engine \
  --query SecretString --output text | \
  python3 -c "import sys,json; print(json.load(sys.stdin)['api_key'])")

curl -X POST "$SIGNAL_URL" \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"strategy_id":"test","symbol":"AAPL","side":"BUY","quantity":1,"order_type":"MKT"}'
# Expected: {"status":"accepted","trade_id":"trade_...","execution_arn":"arn:aws:states:..."}
```

**Check 5: Watch Step Functions execution**
```
AWS Console → Step Functions → State machines → trade-engine-trade-lifecycle-trade-engine
→ Executions tab → click the running execution → watch state transitions
```

---

## Step 9 - Switch to Live Trading

Only after paper trading works correctly for at least **1 week**.

```bash
# Update IB mode secret
aws secretsmanager update-secret \
  --secret-id trade-engine/ib-credentials-trade-engine \
  --secret-string '{"username":"IB_USER","password":"IB_PASS","account_id":"IB_ACCT","mode":"live"}'

# Redeploy with live mode (re-creates EC2 UserData with updated secret)
sam deploy \
  --stack-name trade-engine \
  --region us-east-2 \
  --capabilities CAPABILITY_IAM CAPABILITY_AUTO_EXPAND CAPABILITY_NAMED_IAM \
  --resolve-s3 \
  --no-confirm-changeset \
  --parameter-overrides IbMode=live [... rest of parameters ...]

# Reboot EC2 to pick up new credentials
EC2_ID=$(aws cloudformation describe-stacks \
  --stack-name trade-engine \
  --query "Stacks[0].Outputs[?OutputKey=='Ec2InstanceId'].OutputValue" \
  --output text)
aws ec2 reboot-instances --instance-ids $EC2_ID
# Wait 3 minutes, then check /health
```

---

## Maintenance

### Redeploy after code changes

```bash
cd infra
sam build
sam deploy --stack-name trade-engine --region us-east-2 \
  --capabilities CAPABILITY_IAM CAPABILITY_AUTO_EXPAND CAPABILITY_NAMED_IAM \
  --resolve-s3 --no-confirm-changeset \
  --parameter-overrides [... same parameters ...]
```

### View live logs

```bash
# Any Lambda (replace function name as needed)
aws logs tail /aws/lambda/trade-engine-signal-trade-engine --follow

# All trade-engine Lambdas
aws logs tail /aws/lambda/trade-engine-trade-engine --follow \
  --log-group-pattern trade-engine

# ECS Fargate (dashboard API)
aws logs tail /ecs/trade-engine-dashboard-trade-engine --follow

# Step Functions
aws logs tail /aws/states/trade-engine-trade-lifecycle-trade-engine --follow
```

### Every 90 Days - Rotate API Key

```bash
# Generate and store a new key in Secrets Manager
NEW_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
aws secretsmanager update-secret \
  --secret-id trade-engine/api-key-trade-engine \
  --secret-string "{\"api_key\":\"$NEW_KEY\"}"

# Print it so you can update the Windows env var
echo "New key: $NEW_KEY"
```

Then on your Windows desktop:
1. Win + S → **"Edit the system environment variables"** → Environment Variables
2. Under **User variables**, find `TRADE_ENGINE_API_KEY` → **Edit** → paste the new key
3. Click OK → **restart NinjaTrader**

> No code changes or recompile needed — the key is read from the env var at startup.

### If Your Home IP Changes

```bash
sam deploy ... --parameter-overrides YourDesktopIp="NEW.IP.ADDRESS" [other params]
# Only Lambda authorizer and SG rules change - takes ~60 seconds, no downtime
```

### Destroy Everything

```bash
# Empty S3 bucket first (CloudFormation can't delete non-empty buckets)
BUCKET=$(aws cloudformation describe-stacks \
  --stack-name trade-engine \
  --query "Stacks[0].Outputs[?OutputKey=='S3BucketName'].OutputValue" \
  --output text)
aws s3 rm s3://$BUCKET --recursive

# Then delete the stack (DynamoDB tables have DeletionPolicy: Retain - delete manually if needed)
sam delete --stack-name trade-engine --region us-east-2
```

---

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---|---|---|
| Signal returns 403 | Wrong API key or IP mismatch | Check `TRADE_ENGINE_API_KEY` env var matches Secrets Manager value; verify `YourDesktopIp` param; restart NinjaTrader after any env var change |
| Signal returns 401 | Authorizer Lambda error | Check `/aws/lambda/trade-engine-api-authorizer-trade-engine` logs |
| Dashboard shows "Cannot connect" | `config.js` not updated | Re-run Step 6 |
| Dashboard login loops | Cognito callback URL wrong | Re-run Step 4 Cognito update command |
| Dashboard login "Access Denied" | Wrong Google account | Only `AllowedGoogleEmail` can log in |
| ECS task not starting | ECR image not pushed | Re-run Step 5 |
| ECS task keeps restarting | App crash - check logs | `aws logs tail /ecs/trade-engine-dashboard-trade-engine --follow` |
| CP Gateway not authenticated at market open | EC2 took too long to start | Change `MarketOpenUtc` param 15 minutes earlier and redeploy |
| Step Function stuck at WAIT_FOR_FILL | CP Gateway lost IB session | Check /health; trigger reauth via dashboard |
| No SNS texts | Phone number not confirmed | AWS Console → SNS → Subscriptions → confirm your number |
| Dead man fired unexpectedly | Heartbeat gap > 15min | Check `check_price` Lambda CloudWatch logs |
| sam build fails | Docker not running | Start Docker Desktop, retry |
| SAM deploy timeout | EC2 UserData taking too long | Check EC2 instance System Log in AWS Console |
