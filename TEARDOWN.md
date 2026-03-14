# TEARDOWN.md - Complete Cleanup Guide

This guide covers two scenarios:
1. **Stack-only teardown** - remove trade-engine from an account you are keeping
2. **Full account closure** - nuke the AWS account entirely

---

## Scenario 1 - Stack-Only Teardown

`sam delete` alone is not enough. Several resources survive intentionally or due to
AWS constraints. Follow these steps in order.

### Step 1 - Empty the S3 dashboard bucket

CloudFormation cannot delete a non-empty S3 bucket.

```bash
BUCKET=$(aws cloudformation describe-stacks \
  --stack-name trade-engine \
  --query "Stacks[0].Outputs[?OutputKey=='S3BucketName'].OutputValue" \
  --output text --region us-east-2)

aws s3 rm s3://$BUCKET --recursive
```

### Step 2 - Delete all ECR images

CloudFormation cannot delete an ECR repository that still has images.

```bash
ECR_URL=$(aws cloudformation describe-stacks \
  --stack-name trade-engine \
  --query "Stacks[0].Outputs[?OutputKey=='EcrRepositoryUrl'].OutputValue" \
  --output text --region us-east-2)

REPO_NAME=$(echo $ECR_URL | cut -d/ -f2)

IMAGE_IDS=$(aws ecr list-images \
  --repository-name $REPO_NAME \
  --region us-east-2 \
  --query 'imageIds[*]' --output json)

if [ "$IMAGE_IDS" != "[]" ]; then
  aws ecr batch-delete-image \
    --repository-name $REPO_NAME \
    --image-ids "$IMAGE_IDS" \
    --region us-east-2
fi
```

### Step 3 - Delete the SAM stack

Removes all CloudFormation-managed resources: VPC, subnets, NAT Gateways, security
groups, Lambdas, Step Functions, API Gateway, ALB, ECS, Fargate, Cognito, EventBridge,
SQS, SNS, and the SAM deployment artifact bucket.

```bash
sam delete --stack-name trade-engine --region us-east-2
```

> Takes approximately 15–20 minutes. NAT Gateway deletion is the slowest step.

### Step 4 - Delete DynamoDB tables (DeletionPolicy: Retain)

These tables are intentionally retained by CloudFormation to protect trade history.
Delete only when you are certain you no longer need the data.

```bash
SUFFIX=trade-engine   # matches your --stack-name

for table in trades config positions; do
  aws dynamodb delete-table \
    --table-name "trade-engine-${table}-${SUFFIX}" \
    --region us-east-2 2>/dev/null \
    && echo "Deleted: trade-engine-${table}-${SUFFIX}" \
    || echo "Not found: trade-engine-${table}-${SUFFIX}"
done
```

### Step 5 - Force-delete Secrets Manager secrets

AWS enforces a 7-day recovery window by default. Use `--force-delete-without-recovery`
to delete immediately and free the secret names for re-use.

```bash
SUFFIX=trade-engine

for secret in ib-credentials api-key google-oauth; do
  aws secretsmanager delete-secret \
    --secret-id "trade-engine/${secret}-${SUFFIX}" \
    --force-delete-without-recovery \
    --region us-east-2 2>/dev/null \
    && echo "Deleted: ${secret}" \
    || echo "Not found: ${secret}"
done
```

### Step 6 - Delete CloudWatch Log Groups

Lambda auto-creates log groups that are not tracked by CloudFormation.

```bash
SUFFIX=trade-engine

for fn in signal risk-check place-order wait-for-fill set-stop check-price \
          close-position log-trade tickle portfolio-risk dead-man \
          api-authorizer pre-token config-seed; do
  aws logs delete-log-group \
    --log-group-name "/aws/lambda/trade-engine-${fn}-${SUFFIX}" \
    --region us-east-2 2>/dev/null || true
done

# ECS and Step Functions log groups
aws logs delete-log-group \
  --log-group-name "/ecs/trade-engine-dashboard-${SUFFIX}" \
  --region us-east-2 2>/dev/null || true
aws logs delete-log-group \
  --log-group-name "/aws/states/trade-engine-trade-lifecycle-${SUFFIX}" \
  --region us-east-2 2>/dev/null || true
aws logs delete-log-group \
  --log-group-name "/aws/states/trade-engine-monitoring-loop-${SUFFIX}" \
  --region us-east-2 2>/dev/null || true
```

### Step 7 - Release unattached Elastic IPs

The 3 NAT Gateway EIPs remain allocated to your account after the NAT Gateways are
deleted. They count against your EIP quota and must be explicitly released.

```bash
ALLOC_IDS=$(aws ec2 describe-addresses \
  --filters "Name=domain,Values=vpc" \
  --query "Addresses[?AssociationId==null].AllocationId" \
  --output text --region us-east-2)

for id in $ALLOC_IDS; do
  aws ec2 release-address --allocation-id $id --region us-east-2
  echo "Released EIP: $id"
done
```

> **Caution:** This releases ALL unattached EIPs in the account.
> Skip this step if you have other EIPs you want to keep.

### Step 8 - Verify nothing is left

```bash
# EC2 - should return empty
aws ec2 describe-instances \
  --filters "Name=tag:project,Values=trade-engine" \
            "Name=instance-state-name,Values=running,stopped" \
  --query 'Reservations[].Instances[].InstanceId' \
  --output text --region us-east-2

# ECS - should return nothing
aws ecs list-clusters --region us-east-2 \
  --query "clusterArns[?contains(@,'trade-engine')]" --output text

# DynamoDB - should return nothing
aws dynamodb list-tables --region us-east-2 \
  --query "TableNames[?contains(@,'trade-engine')]" --output text

# Secrets - should return nothing
aws secretsmanager list-secrets --region us-east-2 \
  --query "SecretList[?contains(Name,'trade-engine')].Name" --output text
```

---

## Scenario 2 - Full AWS Account Closure

If you are closing the AWS account entirely, AWS guarantees a full wipe of all
resources and data. You do not need to run any of the steps above first.

### What happens when you close an account

- All running resources (EC2, ECS, Lambda, etc.) are terminated immediately upon closure.
- All stored data (S3, DynamoDB, Secrets Manager, CloudWatch Logs, ECR images) is
  permanently deleted within 90 days of account closure.
- All IAM users, roles, and policies are deleted.
- All networking resources (VPCs, subnets, EIPs, security groups) are deleted.
- The account ID is retired permanently and never reused.
- A new AWS account you open later starts completely clean with no connection to the
  old account.

### Charges at and after closure

| Situation | Charged? |
|---|---|
| Resources running at the moment of closure | Yes - billed up to the hour/minute they are terminated |
| S3 storage in the current billing month | Yes - pro-rated for days used |
| NAT Gateway / ALB hours in current month | Yes - up to the hour of termination |
| Secrets Manager API calls in current month | Yes - whatever accrued before closure |
| Any charges after closure | No - billing stops completely |
| Secrets Manager 7-day recovery window | Not applicable - account closure bypasses it |
| **Reserved Instances or Savings Plans** | **Yes - you are still billed for the full committed term even after closure** |

> **Critical:** This project uses only on-demand resources - no Reserved Instances,
> no Savings Plans, no Dedicated Hosts. There are no lingering financial commitments.
> Your final bill will be one normal monthly invoice covering usage up to the closure
> date, then nothing further.

### How to close an account

```
AWS Console
  → Click your account name (top-right corner)
  → Account
  → Scroll to the bottom
  → "Close Account"
  → Read and check every acknowledgement checkbox
  → Confirm
```

> You must be signed in as the **root user** (the email address used to create the
> account). IAM users and roles cannot close accounts, even with AdministratorAccess.

---

## Quick Reference

| Resource | Survives `sam delete`? | Reason | Fix |
|---|---|---|---|
| DynamoDB tables | Yes | `DeletionPolicy: Retain` | Step 4 |
| Secrets Manager secrets | Yes (7 days) | AWS recovery window | Step 5 |
| Lambda CloudWatch Log Groups | Yes | Auto-created, not in template | Step 6 |
| NAT Gateway EIPs (×3) | Yes | Released but not freed | Step 7 |
| ECR images | Blocks deletion | Non-empty repo cannot be deleted | Step 2 |
| S3 dashboard bucket | Blocks deletion | Non-empty bucket cannot be deleted | Step 1 |
| SAM artifact bucket | No | `sam delete` handles this | - |
| VPC / subnets / SGs | No | CloudFormation-managed | - |
| EC2 / ECS / ALB / Lambda | No | CloudFormation-managed | - |
| Cognito User Pool | No | CloudFormation-managed | - |
| SNS topic | No | CloudFormation-managed | - |
