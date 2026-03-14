"""
ALB Manager Lambda - creates or deletes the dashboard ALB on market open/close.

This Lambda runs OUTSIDE the VPC so it can reach the ELBv2, CloudFront, and SSM
public APIs (none of which have Interface VPC endpoints we can route through).

Called by EventBridge at market open (action=START) and market close (action=STOP).

On START:
  1. Create the ALB with the configured subnets and security group.
  2. Create an HTTP->HTTPS redirect listener on port 80.
  3. Create a direct HTTP listener on port 8080 forwarding to the ECS target group.
  4. Update the CloudFront distribution's ALB origin to the new DNS name.
  5. Store the new ALB ARN in SSM so STOP knows what to delete.

On STOP:
  1. Read the ALB ARN from SSM (or look it up by name if SSM entry is missing).
  2. Delete the ALB. Listeners are automatically deleted with it.
  3. The ECS target group is left intact - task IPs remain registered and will be
     immediately available when the ALB is recreated next morning.

Cost impact:
  ALB hourly charge of $0.008 applies only while the resource exists.
  157.5 market hours vs 720 calendar hours saves ~$4.50/month.

One-time setup after first deploy:
  The initial deploy creates an ALB via CloudFormation. Once the stack is up,
  the ALB can be "handed off" to this Lambda by running:
    aws lambda invoke --function-name trade-engine-alb-manager-<stack> \\
      --payload '{"action":"STOP"}' /dev/null
  From that point the Lambda manages the ALB lifecycle each trading day.
"""
import os
import json
import logging
import boto3
import botocore

logger = logging.getLogger()
logger.setLevel(logging.INFO)

elbv2      = boto3.client("elbv2")
cf_client  = boto3.client("cloudfront")
ssm        = boto3.client("ssm")

ALB_NAME           = os.environ["ALB_NAME"]
PUBLIC_SUBNET_1    = os.environ["PUBLIC_SUBNET_1"]
PUBLIC_SUBNET_2    = os.environ["PUBLIC_SUBNET_2"]
PUBLIC_SUBNET_3    = os.environ["PUBLIC_SUBNET_3"]
SG_ALB             = os.environ["SG_ALB"]
TARGET_GROUP_ARN   = os.environ["TARGET_GROUP_ARN"]
CLOUDFRONT_DIST_ID = os.environ["CLOUDFRONT_DIST_ID"]
ALB_ARN_PARAM      = os.environ["ALB_ARN_PARAM"]   # SSM parameter name


def handler(event, context):
    action = event.get("action", "").upper()
    logger.info(f"ALB manager - action={action}")

    if action == "START":
        return _start()
    elif action == "STOP":
        return _stop()
    else:
        raise ValueError(f"Unknown action '{action}'. Expected START or STOP.")


# --- START ---

def _start():
    # If the ALB already exists (e.g. first morning after initial CFN deploy),
    # just update CloudFront to make sure the origin is correct and move on.
    existing = _describe_alb_by_name(ALB_NAME)
    if existing:
        dns = existing["DNSName"]
        arn = existing["LoadBalancerArn"]
        logger.info(f"ALB already exists ({arn}), updating CloudFront origin")
        _store_arn(arn)
        _update_cloudfront_origin(dns)
        return {"status": "already_exists", "dns": dns, "arn": arn}

    # Create the load balancer
    resp = elbv2.create_load_balancer(
        Name=ALB_NAME,
        Subnets=[PUBLIC_SUBNET_1, PUBLIC_SUBNET_2, PUBLIC_SUBNET_3],
        SecurityGroups=[SG_ALB],
        Scheme="internet-facing",
        Type="application",
        Tags=[{"Key": "project", "Value": "trade-engine"}],
    )
    alb      = resp["LoadBalancers"][0]
    alb_arn  = alb["LoadBalancerArn"]
    alb_dns  = alb["DNSName"]
    logger.info(f"ALB created: {alb_dns} ({alb_arn})")

    # Wait for the ALB to be active before attaching listeners
    waiter = elbv2.get_waiter("load_balancer_available")
    waiter.wait(LoadBalancerArns=[alb_arn], WaiterConfig={"Delay": 5, "MaxAttempts": 24})

    # Port 80 - redirect to HTTPS (CloudFront always connects on 8080 but browsers
    # hitting the ALB directly should land on HTTPS)
    elbv2.create_listener(
        LoadBalancerArn=alb_arn,
        Protocol="HTTP",
        Port=80,
        DefaultActions=[{
            "Type": "redirect",
            "RedirectConfig": {
                "Protocol": "HTTPS",
                "Port": "443",
                "StatusCode": "HTTP_301",
            },
        }],
    )

    # Port 8080 - forward to ECS target group (CloudFront uses this)
    elbv2.create_listener(
        LoadBalancerArn=alb_arn,
        Protocol="HTTP",
        Port=8080,
        DefaultActions=[{
            "Type": "forward",
            "TargetGroupArn": TARGET_GROUP_ARN,
        }],
    )

    _store_arn(alb_arn)
    _update_cloudfront_origin(alb_dns)

    logger.info("ALB start complete")
    return {"status": "created", "dns": alb_dns, "arn": alb_arn}


# --- STOP ---

def _stop():
    alb_arn = _read_arn_from_ssm()

    if not alb_arn:
        # SSM entry missing - try looking up by name
        existing = _describe_alb_by_name(ALB_NAME)
        if not existing:
            logger.info("No ALB found to delete - nothing to do")
            return {"status": "not_found"}
        alb_arn = existing["LoadBalancerArn"]

    elbv2.delete_load_balancer(LoadBalancerArn=alb_arn)
    logger.info(f"ALB deleted: {alb_arn}")
    # Clear SSM entry so next START doesn't try a stale ARN
    try:
        ssm.delete_parameter(Name=ALB_ARN_PARAM)
    except ssm.exceptions.ParameterNotFound:
        pass

    return {"status": "deleted", "arn": alb_arn}


# --- Helpers ---

def _describe_alb_by_name(name: str):
    try:
        resp = elbv2.describe_load_balancers(Names=[name])
        return resp["LoadBalancers"][0] if resp["LoadBalancers"] else None
    except elbv2.exceptions.LoadBalancerNotFoundException:
        return None


def _store_arn(arn: str):
    ssm.put_parameter(
        Name=ALB_ARN_PARAM,
        Value=arn,
        Type="String",
        Overwrite=True,
    )


def _read_arn_from_ssm() -> str | None:
    try:
        return ssm.get_parameter(Name=ALB_ARN_PARAM)["Parameter"]["Value"]
    except (ssm.exceptions.ParameterNotFound, botocore.exceptions.ClientError):
        return None


def _update_cloudfront_origin(alb_dns: str):
    """
    Update the CloudFront distribution's ALB origin domain to point at the
    newly created (or existing) ALB. CloudFront caches origin DNS so we must
    update it each time the ALB is recreated.
    """
    dist  = cf_client.get_distribution(Id=CLOUDFRONT_DIST_ID)
    config = dist["Distribution"]["DistributionConfig"]
    etag   = dist["ETag"]

    updated = False
    for origin in config["Origins"]["Items"]:
        if origin.get("Id") == "AlbOrigin":
            origin["DomainName"] = alb_dns
            updated = True
            logger.info(f"CloudFront origin AlbOrigin -> {alb_dns}")
            break

    if not updated:
        logger.warning("AlbOrigin not found in CloudFront distribution - check origin ID")
        return

    cf_client.update_distribution(
        DistributionConfig=config,
        Id=CLOUDFRONT_DIST_ID,
        IfMatch=etag,
    )
    logger.info("CloudFront distribution updated")
